#!/bin/bash

CHARM="glance"

SERVICES="glance-api glance-registry"
PACKAGES="glance python-mysqldb python-swift python-keystone uuid haproxy"

GLANCE_REGISTRY_CONF="/etc/glance/glance-registry.conf"
GLANCE_REGISTRY_PASTE_INI="/etc/glance/glance-registry-paste.ini"
GLANCE_API_CONF="/etc/glance/glance-api.conf"
GLANCE_API_PASTE_INI="/etc/glance/glance-api-paste.ini"
CONF_DIR="/etc/glance"
HOOKS_DIR="$CHARM_DIR/hooks"

# Flag used to track config changes.
CONFIG_CHANGED="False"
if [[ -e "$HOOKS_DIR/lib/openstack-common" ]] ; then
  . $HOOKS_DIR/lib/openstack-common
else
  juju-log "ERROR: Couldn't load $HOOKS_DIR/lib/openstack-common." && exit 1
fi

function set_paste_deploy_flavor {
  # NOTE(adam_g): If we want to benefit from CONFIG_CHANGED here,
  # needs to be updated to detect already config'd settings.
  local flavor="$1"
  local config="$2"
  case $config in
    "api") local conf=$GLANCE_API_CONF ;;
    "registry") local conf=$GLANCE_REGISTRY_CONF ;;
    *) juju-log "ERROR: set_paste_deploy: invalid config=$config" && exit 1 ;;
  esac
  if ! grep -q "\[paste_deploy\]" "$conf" ; then
    juju-log "Updating $conf: Setting new paste_deploy flavor = $flavor"
    echo -e "\n[paste_deploy]\nflavor = keystone\n" >>$conf &&
      CONFIG_CHANGED="True" && return 0
    juju-log "ERROR: Could not update paste_deploy flavor in $conf" && return 1
  fi
  juju-log "Updating $conf: Setting paste_deploy flavor = $flavor"
  local tag="[paste_deploy]"
  sed -i "/$tag/, +1 s/\(flavor = \).*/\1$flavor/g" $conf &&
    CONFIG_CHANGED="True" && return 0
  juju-log "ERROR: Could not update paste_deploy flavor in $conf" && return 1
}

function update_pipeline {
  # updates pipeline middleware definitions in api-paste.ini
  local pipeline="$1"
  local new="$2"
  local config="$3"

  case $config in
    "api") local api_conf=$GLANCE_API_CONF ;;
    "registry") local api_conf=$GLANCE_REGISTRY_CONF ;;
    *) juju-log "ERROR: update_pipeline: invalid config=$config" && exit 1 ;;
  esac

  local tag="\[pipeline:$pipeline\]"
  if ! grep -q "$tag" $api_conf ; then
      juju-log "ERROR: update_pipeline: pipeline not found: $pipeline"
      return 1
  fi
  juju-log "Updating pipeline:$pipeline in $api_conf"
  sed -i "/$tag/, +1 s/\(pipeline = \).*/\1$new/g" $api_conf
}

function set_or_update {
  # This handles configuration of both api and registry server
  # until LP #806241 is resolved.  Until then, $3 is either
  # 'api' or 'registry' to specify which
  # set or update a key=value config option in glance.conf
  KEY=$1
  VALUE=$2
  case "$3" in
    "api") CONF=$GLANCE_API_CONF ;;
    "api-paste") CONF=$GLANCE_API_PASTE_INI ;;
    "registry") CONF=$GLANCE_REGISTRY_CONF ;;
    "registry-paste") CONF=$GLANCE_REGISTRY_PASTE_INI ;;
    *) juju-log "ERROR: set_or_update(): Invalid or no config file specified." \
        && exit 1 ;;
  esac
  [[ -z $KEY ]] && juju-log "ERROR: set_or_update(): value $VALUE missing key" \
        && exit 1
  [[ -z $VALUE ]] && juju-log "ERROR: set_or_update(): key $KEY missing value" \
        && exit 1
  cat $CONF | grep "$KEY = $VALUE" >/dev/null \
   && juju-log "glance: $KEY = $VALUE already set" && return 0
  if cat $CONF | grep "$KEY =" >/dev/null ; then
    sed -i "s|\($KEY = \).*|\1$VALUE|" $CONF
  else
    echo "$KEY = $VALUE" >>$CONF
  fi
  CONFIG_CHANGED="True"
}

do_openstack_upgrade() {
  # update openstack components to those provided by a new installation source
  # it is assumed the calling hook has confirmed that the upgrade is sane.
  local rel="$1"
  shift
  local packages=$@
  orig_os_rel=$(get_os_codename_package "glance-common")
  new_rel=$(get_os_codename_install_source "$rel")

  # Backup the config directory.
  local stamp=$(date +"%Y%m%d%M%S")
  tar -pcf /var/lib/juju/$CHARM-backup-$stamp.tar $CONF_DIR

  # Setup apt repository access and kick off the actual package upgrade.
  configure_install_source "$rel"
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get --option Dpkg::Options::=--force-confnew -y \
     install --no-install-recommends $packages

  # Update the new config files for existing relations.
  local r_id=""

  r_id=$(relation-ids shared-db)
  if [[ -n "$r_id" ]] ; then
    juju-log "$CHARM: Configuring database after upgrade to $rel."
    db_changed $r_id
  fi

  r_id=$(relation-ids identity-service)
  if [[ -n "$r_id" ]] ; then
    juju-log "$CHARM: Configuring identity service after upgrade to $rel."
    keystone_changed $r_id
  fi

  local ceph_ids="$(relation-ids ceph)"
  [[ -n "$ceph_ids" ]] && apt-get -y install ceph-common python-ceph
  for r_id in $ceph_ids ; do
    for unit in $(relation-list -r $r_id) ; do
      ceph_changed "$r_id" "$unit"
    done
  done

  [[ -n "$(relation-ids object-store)" ]] && object-store_joined
}

configure_https() {
  # request openstack-common setup reverse proxy mapping for API and registry
  # servers
  service_ctl glance-api stop
  if [[ -n "$(peer_units)" ]] || is_clustered ; then
    # haproxy may already be configured. need to push it back in the request
    # pipeline in preparation for a change from:
    #  from:  haproxy (9292) -> glance_api (9282)
    #  to:    ssl (9292) -> haproxy (9291) -> glance_api (9272)
    local next_server=$(determine_haproxy_port 9292)
    local api_port=$(determine_api_port 9292)
    configure_haproxy "glance_api:$next_server:$api_port"
  else
    # if not clustered, the glance-api is next in the pipeline.
    local api_port=$(determine_api_port 9292)
    local next_server=$api_port
  fi

  # setup https to point to either haproxy or directly to api server, depending.
  setup_https 9292:$next_server

  # configure servers to listen on new ports accordingly.
  set_or_update bind_port "$api_port" "api"
  service_ctl all start

  local r_id=""
  # (re)configure ks endpoint accordingly in ks and nova.
  for r_id in $(relation-ids identity-service) ; do
    keystone_joined "$r_id"
  done
  for r_id in $(relation-ids image-service) ; do
    image-service_joined "$r_id"
  done
}
