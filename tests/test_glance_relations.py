from mock import call, patch, MagicMock

from tests.test_utils import CharmTestCase

import hooks.glance_utils as utils

_reg = utils.register_configs
_map = utils.restart_map

utils.register_configs = MagicMock()
utils.restart_map = MagicMock()

import hooks.glance_relations as relations

utils.register_configs = _reg
utils.restart_map = _map

TO_PATCH = [
    # charmhelpers.core.hookenv
    'Hooks',
    'config',
    'juju_log',
    'relation_ids',
    'relation_set',
    'relation_get',
    'service_name',
    'unit_get',
    # charmhelpers.core.host
    'apt_install',
    'apt_update',
    'restart_on_change',
    'service_stop',
    #charmhelpers.contrib.openstack.utils
    'configure_installation_source',
    'get_os_codename_install_source',
    'get_os_codename_package',
    'openstack_upgrade_available',
    # charmhelpers.contrib.hahelpers.cluster_utils
    'eligible_leader',
    'is_clustered',
    # glance_utils
    'restart_map',
    'register_configs',
    'do_openstack_upgrade',
    'migrate_database',
    'ensure_ceph_keyring',
    'ensure_ceph_pool',
    # other
    'getstatusoutput',
    'check_call',
]


class GlanceRelationTests(CharmTestCase):
    def setUp(self):
        super(GlanceRelationTests, self).setUp(relations, TO_PATCH)
        self.config.side_effect = self.test_config.get

    def test_install_hook(self):
        repo = 'cloud:precise-grizzly'
        self.test_config.set('openstack-origin', repo)
        self.service_stop.return_value = True
        relations.install_hook()
        self.configure_installation_source.assert_called_with(repo)
        self.assertTrue(self.apt_update.called)
        self.apt_install.assert_called_with(['apache2', 'glance', 'python-mysqldb',
                                             'python-swift', 'python-keystone',
                                             'uuid', 'haproxy'])

    def test_db_joined(self):
        self.unit_get.return_value = 'glance.foohost.com'
        relations.db_joined()
        self.relation_set.assert_called_with(database='glance', username='glance',
                                             hostname='glance.foohost.com')
        self.unit_get.assert_called_with('private-address')

    @patch.object(relations, 'CONFIGS')
    def test_db_changed_missing_relation_data(self, configs):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = []
        relations.db_changed()
        self.juju_log.assert_called_with(
            'shared-db relation incomplete. Peer not ready?'
        )

    def _shared_db_test(self, configs):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['shared-db']
        configs.write = MagicMock()
        relations.db_changed()

    @patch.object(relations, 'CONFIGS')
    def test_db_changed_no_essex(self, configs):
        self._shared_db_test(configs)
        self.assertEquals([call('/etc/glance/glance-registry.conf'),
                           call('/etc/glance/glance-api.conf')],
                           configs.write.call_args_list)
        self.juju_log.assert_called_with(
            'Cluster leader, performing db sync'
        )
        self.migrate_database.assert_called_with()

    @patch.object(relations, 'CONFIGS')
    def test_db_changed_with_essex_not_setting_version_control(self, configs):
        self.get_os_codename_package.return_value = "essex"
        self.getstatusoutput.return_value = (0, "version")
        self._shared_db_test(configs)
        self.assertEquals([call('/etc/glance/glance-registry.conf')],
                           configs.write.call_args_list)
        self.juju_log.assert_called_with(
            'Cluster leader, performing db sync'
        )
        self.migrate_database.assert_called_with()

    @patch.object(relations, 'CONFIGS')
    def test_db_changed_with_essex_setting_version_control(self, configs):
        self.get_os_codename_package.return_value = "essex"
        self.getstatusoutput.return_value = (1, "version")
        self._shared_db_test(configs)
        self.assertEquals([call('/etc/glance/glance-registry.conf')],
                           configs.write.call_args_list)
        self.check_call.assert_called_with(
            ["glance-manage", "version_control", "0"]
        )
        self.juju_log.assert_called_with(
            'Cluster leader, performing db sync'
        )
        self.migrate_database.assert_called_with()

    @patch.object(relations, 'CONFIGS')
    def test_image_service_joined_clustered_with_https(self, configs):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['https']
        configs.write = MagicMock()
        self.unit_get.return_value = 'glance.foohost.com'
        self.is_clustered.return_value = True
        self.test_config.set('vip', '10.10.10.10')
        relations.image_service_joined()
        self.assertTrue(self.eligible_leader.called)
        self.unit_get.assert_called_with('private-address')
        self.relation_set.assert_called_with(relation_id=None,
                                             glance_api_server="https://10.10.10.10:9292")

    @patch.object(relations, 'CONFIGS')
    def test_image_service_joined_not_clustered_with_https(self, configs):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['https']
        configs.write = MagicMock()
        self.unit_get.return_value = 'glance.foohost.com'
        self.is_clustered.return_value = False
        relations.image_service_joined()
        self.assertTrue(self.eligible_leader.called)
        self.unit_get.assert_called_with('private-address')
        self.relation_set.assert_called_with(relation_id=None,
                                             glance_api_server="https://glance.foohost.com:9292")

    @patch.object(relations, 'CONFIGS')
    def test_image_service_joined_clustered_with_http(self, configs):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['']
        configs.write = MagicMock()
        self.unit_get.return_value = 'glance.foohost.com'
        self.is_clustered.return_value = True
        self.test_config.set('vip', '10.10.10.10')
        relations.image_service_joined()
        self.assertTrue(self.eligible_leader.called)
        self.unit_get.assert_called_with('private-address')
        self.relation_set.assert_called_with(relation_id=None,
                                             glance_api_server="http://10.10.10.10:9292")

    @patch.object(relations, 'CONFIGS')
    def test_image_service_joined_not_clustered_with_http(self, configs):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = []
        configs.write = MagicMock()
        self.unit_get.return_value = 'glance.foohost.com'
        self.is_clustered.return_value = False
        relations.image_service_joined()
        self.assertTrue(self.eligible_leader.called)
        self.unit_get.assert_called_with('private-address')
        self.relation_set.assert_called_with(relation_id=None,
                                             glance_api_server="http://glance.foohost.com:9292")

    @patch.object(relations, 'CONFIGS')
    def test_object_store_joined_without_identity_service(self, configs):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['']
        configs.write = MagicMock()
        relations.object_store_joined()
        self.juju_log.assert_called_with(
            'Deferring swift stora configuration until '
            'an identity-service relation exists'
        )

    @patch.object(relations, 'CONFIGS')
    def test_object_store_joined_with_identity_service_without_object_store(self, configs):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['identity-service']
        configs.write = MagicMock()
        relations.object_store_joined()
        self.juju_log.assert_called_with(
            'swift relation incomplete'
        )

    @patch.object(relations, 'CONFIGS')
    def test_object_store_joined_with_identity_service_with_object_store(self, configs):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['identity-service', 'object-store']
        configs.write = MagicMock()
        relations.object_store_joined()
        self.assertEquals([call('/etc/glance/glance-api.conf')],
                           configs.write.call_args_list)

    @patch('os.mkdir')
    @patch('os.path.isdir')
    def test_ceph_joined(self, isdir, mkdir):
        isdir.return_value = False
        relations.ceph_joined()
        mkdir.assert_called_with('/etc/ceph')
        self.apt_install.assert_called_with(['ceph-common', 'python-ceph'])

    @patch.object(relations, 'CONFIGS')
    def test_ceph_changed_missing_relation_data(self, configs):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = []
        configs.write = MagicMock()
        relations.ceph_changed()
        self.juju_log.assert_called_with(
            'ceph relation incomplete. Peer not ready?'
        )

    @patch.object(relations, 'CONFIGS')
    def test_ceph_changed_no_keyring(self, configs):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['ceph']
        configs.write = MagicMock()
        self.ensure_ceph_keyring.return_value = False
        relations.ceph_changed()
        self.juju_log.assert_called_with(
            'Could not create ceph keyring: peer not ready?'
        )

    @patch.object(relations, 'CONFIGS')
    def test_ceph_changed_with_key_and_relation_data(self, configs):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['ceph']
        configs.write = MagicMock()
        self.ensure_ceph_keyring.return_value = True
        relations.ceph_changed()
        self.assertEquals([call('/etc/glance/glance-api.conf'),
                           call('/etc/ceph/ceph.conf')],
                           configs.write.call_args_list)
        self.ensure_ceph_pool.assert_called_with(service=self.service_name())

    @patch.object(relations, 'CONFIGS')
    def test_keystone_joined_not_clustered(self, configs):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['']
        configs.write = MagicMock()
        self.unit_get.return_value = 'glance.foohost.com'
        self.test_config.set('region', 'FirstRegion')
        self.is_clustered.return_value = False
        relations.keystone_joined()
        self.unit_get.assert_called_with('private-address')
        self.relation_set.assert_called_with(
            relation_id=None,
            service='glance',
            region='FirstRegion',
            public_url='http://glance.foohost.com:9292',
            admin_url='http://glance.foohost.com:9292',
            internal_url='http://glance.foohost.com:9292',
        )

    @patch.object(relations, 'CONFIGS')
    def test_keystone_joined_clustered(self, configs):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['']
        configs.write = MagicMock()
        self.unit_get.return_value = 'glance.foohost.com'
        self.test_config.set('region', 'FirstRegion')
        self.test_config.set('vip', '10.10.10.10')
        self.is_clustered.return_value = True
        relations.keystone_joined()
        self.unit_get.assert_called_with('private-address')
        self.relation_set.assert_called_with(
            relation_id=None,
            service='glance',
            region='FirstRegion',
            public_url='http://10.10.10.10:9292',
            admin_url='http://10.10.10.10:9292',
            internal_url='http://10.10.10.10:9292',
        )


    @patch.object(relations, 'CONFIGS')
    def test_keystone_joined_not_clustered_with_https(self, configs):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['https']
        configs.write = MagicMock()
        self.unit_get.return_value = 'glance.foohost.com'
        self.test_config.set('region', 'FirstRegion')
        self.is_clustered.return_value = False
        relations.keystone_joined()
        self.unit_get.assert_called_with('private-address')
        self.relation_set.assert_called_with(
            relation_id=None,
            service='glance',
            region='FirstRegion',
            public_url='https://glance.foohost.com:9292',
            admin_url='https://glance.foohost.com:9292',
            internal_url='https://glance.foohost.com:9292',
        )

    @patch.object(relations, 'CONFIGS')
    def test_keystone_joined_clustered_with_https(self, configs):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['https']
        configs.write = MagicMock()
        self.unit_get.return_value = 'glance.foohost.com'
        self.test_config.set('region', 'FirstRegion')
        self.test_config.set('vip', '10.10.10.10')
        self.is_clustered.return_value = True
        relations.keystone_joined()
        self.unit_get.assert_called_with('private-address')
        self.relation_set.assert_called_with(
            relation_id=None,
            service='glance',
            region='FirstRegion',
            public_url='https://10.10.10.10:9292',
            admin_url='https://10.10.10.10:9292',
            internal_url='https://10.10.10.10:9292',
        )

    @patch.object(relations, 'configure_https')
    @patch.object(relations, 'CONFIGS')
    def test_keystone_changed_no_object_store_relation(self, configs, configure_https):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['identity-service']
        configs.write = MagicMock()
        self.relation_ids.return_value = False
        relations.keystone_changed()
        self.assertEquals([call('/etc/glance/glance-api.conf'),
                           call('/etc/glance/glance-registry.conf'),
                           call('/etc/glance/glance-api-paste.ini'),
                           call('/etc/glance/glance-registry-paste.ini')],
                           configs.write.call_args_list)
        self.assertTrue(configure_https.called)

    @patch.object(relations, 'configure_https')
    @patch.object(relations, 'object_store_joined')
    @patch.object(relations, 'CONFIGS')
    def test_keystone_changed_no_object_store_relation(self, configs, object_store_joined, configure_https):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['identity-service']
        configs.write = MagicMock()
        self.relation_ids.return_value = True
        relations.keystone_changed()
        self.assertEquals([call('/etc/glance/glance-api.conf'),
                           call('/etc/glance/glance-registry.conf'),
                           call('/etc/glance/glance-api-paste.ini'),
                           call('/etc/glance/glance-registry-paste.ini')],
                           configs.write.call_args_list)
        object_store_joined.assert_called_with()
        self.assertTrue(configure_https.called)

    @patch.object(relations, 'configure_https')
    def test_config_changed_no_openstack_upgrade(self, configure_https):
        self.openstack_upgrade_available.return_value = False
        relations.config_changed()
        self.assertTrue(configure_https.called)

    @patch.object(relations, 'configure_https')
    def test_config_changed_with_openstack_upgrade(self, configure_https):
        self.openstack_upgrade_available.return_value = True
        relations.config_changed()
        self.juju_log.assert_called_with(
            'Upgrading OpenStack release'
        )
        self.assertTrue(self.do_openstack_upgrade.called)
        self.assertTrue(configure_https.called)

    @patch.object(relations, 'CONFIGS')
    def test_cluster_changed(self, configs):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['cluster']
        configs.write = MagicMock()
        relations.cluster_changed()
        self.assertEquals([call('/etc/glance/glance-api.conf'),
                           call('/etc/haproxy/haproxy.cfg')],
                           configs.write.call_args_list)

    @patch.object(relations, 'cluster_changed')
    def test_upgrade_charm(self, cluster_changed):
        relations.upgrade_charm()
        cluster_changed.assert_called_with()

    def test_ha_relation_joined(self):
        self.test_config.set('ha-bindiface', 'em0')
        self.test_config.set('ha-mcastport', '8080')
        self.test_config.set('vip', '10.10.10.10')
        self.test_config.set('vip_iface', 'em1')
        self.test_config.set('vip_cidr', '24')
        relations.ha_relation_joined()
        args = {
            'corosync_bindiface': 'em0',
            'corosync_mcastport': '8080',
            'init_services': {'res_glance_haproxy': 'haproxy'},
            'resources': {'res_glance_vip': 'ocf:heartbeat:IPaddr2',
                          'res_glance_haproxy': 'lsb:haproxy'},
            'resource_params': {'res_glance_vip': 'params ip="10.10.10.10" cidr_netmask="24" nic="em1"',
                                'res_glance_haproxy': 'op monitor interval="5s"'},
            'clones': {'cl_glance_haproxy': 'res_glance_haproxy'}
        }
        self.relation_set.assert_called_with(**args)

    def test_ha_relation_changed_not_clustered(self):
        self.relation_get.return_value = False
        relations.ha_relation_changed()
        self.juju_log.assert_called_with('glance subordinate is not fully clustered.')

    @patch.object(relations, 'CONFIGS')
    def test_ha_relation_changed_with_https(self, configs):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['https']
        configs.write = MagicMock()
        self.relation_get.return_value = True
        self.test_config.set('vip', '10.10.10.10')
        self.test_config.set('region', 'FirstRegion')
        self.relation_ids.return_value = ['relation-made:0']
        relations.ha_relation_changed()
        self.juju_log.assert_called_with('glance: Cluster configured, notifying other services')
        self.assertEquals([call('identity-service'), call('image-service')],
                          self.relation_ids.call_args_list)
        ex = [
            call(service='glance',
                 region='FirstRegion',
                 public_url='https://10.10.10.10:9292',
                 internal_url='https://10.10.10.10:9292',
                 relation_id='relation-made:0',
                 admin_url='https://10.10.10.10:9292'),
            call(glance_api_server='https://10.10.10.10:9292',
                 relation_id='relation-made:0')
        ]
        self.assertEquals(ex, self.relation_set.call_args_list)

    @patch.object(relations, 'CONFIGS')
    def test_ha_relation_changed_with_http(self, configs):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['']
        configs.write = MagicMock()
        self.relation_get.return_value = True
        self.test_config.set('vip', '10.10.10.10')
        self.test_config.set('region', 'FirstRegion')
        self.relation_ids.return_value = ['relation-made:0']
        relations.ha_relation_changed()
        self.juju_log.assert_called_with('glance: Cluster configured, notifying other services')
        self.assertEquals([call('identity-service'), call('image-service')],
                          self.relation_ids.call_args_list)
        ex = [
            call(service='glance',
                 region='FirstRegion',
                 public_url='http://10.10.10.10:9292',
                 internal_url='http://10.10.10.10:9292',
                 relation_id='relation-made:0',
                 admin_url='http://10.10.10.10:9292'),
            call(glance_api_server='http://10.10.10.10:9292',
                 relation_id='relation-made:0')
        ]
        self.assertEquals(ex, self.relation_set.call_args_list)

    @patch.object(relations, 'keystone_joined')
    @patch.object(relations, 'CONFIGS')
    def test_configure_https_enable_with_identity_service(self, configs, keystone_joined):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['https']
        configs.write = MagicMock()
        self.relation_ids.return_value = ['identity-service:0']
        relations.configure_https()
        cmd = ['a2ensite', 'openstack_https_frontend']
        self.check_call.assert_called_with(cmd)
        keystone_joined.assert_called_with(relation_id='identity-service:0')

    @patch.object(relations, 'keystone_joined')
    @patch.object(relations, 'CONFIGS')
    def test_configure_https_disable_with_keystone_joined(self, configs, keystone_joined):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['']
        configs.write = MagicMock()
        self.relation_ids.return_value = ['identity-service:0']
        relations.configure_https()
        cmd = ['a2dissite', 'openstack_https_frontend']
        self.check_call.assert_called_with(cmd)
        keystone_joined.assert_called_with(relation_id='identity-service:0')

    @patch.object(relations, 'image_service_joined')
    @patch.object(relations, 'CONFIGS')
    def test_configure_https_enable_with_image_service(self, configs, image_service_joined):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['https']
        configs.write = MagicMock()
        self.relation_ids.return_value = ['image-service:0']
        relations.configure_https()
        cmd = ['a2ensite', 'openstack_https_frontend']
        self.check_call.assert_called_with(cmd)
        image_service_joined.assert_called_with(relation_id='image-service:0')

    @patch.object(relations, 'image_service_joined')
    @patch.object(relations, 'CONFIGS')
    def test_configure_https_disable_with_image_service(self, configs, image_service_joined):
        configs.complete_contexts = MagicMock()
        configs.complete_contexts.return_value = ['']
        configs.write = MagicMock()
        self.relation_ids.return_value = ['image-service:0']
        relations.configure_https()
        cmd = ['a2dissite', 'openstack_https_frontend']
        self.check_call.assert_called_with(cmd)
        image_service_joined.assert_called_with(relation_id='image-service:0')

