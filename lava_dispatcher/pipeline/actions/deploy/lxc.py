# Copyright (C) 2015 Linaro Limited
#
# Author: Senthil Kumaran S <senthil.kumaran@linaro.org>
#
# This file is part of LAVA Dispatcher.
#
# LAVA Dispatcher is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# LAVA Dispatcher is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along
# with this program; if not, see <http://www.gnu.org/licenses>.

import os
from lava_dispatcher.pipeline.logical import Deployment
from lava_dispatcher.pipeline.action import (
    Action,
    ConfigurationError,
    JobError,
    LAVABug,
    Pipeline,
)
from lava_dispatcher.pipeline.actions.deploy import DeployAction
from lava_dispatcher.pipeline.actions.deploy.overlay import OverlayAction
from lava_dispatcher.pipeline.actions.deploy.apply_overlay import ApplyLxcOverlay
from lava_dispatcher.pipeline.actions.deploy.environment import DeployDeviceEnvironment
from lava_dispatcher.pipeline.actions.boot.lxc import (
    LxcStartAction,
    LxcStopAction,
)
from lava_dispatcher.pipeline.utils.shell import infrastructure_error
from lava_dispatcher.pipeline.protocols.lxc import LxcProtocol
from lava_dispatcher.pipeline.utils.constants import (
    LXC_PATH,
    LXC_TEMPLATE_WITH_MIRROR,
    LXC_DEFAULT_PACKAGES,
    UDEV_RULES_DIR,
)
from lava_dispatcher.pipeline.utils.udev import lxc_udev_rule
from lava_dispatcher.pipeline.utils.filesystem import (
    debian_package_version,
    lxc_path,
)

# pylint: disable=superfluous-parens


class Lxc(Deployment):
    """
    Strategy class for a lxc deployment.
    Downloads the relevant parts, copies to the locations using lxc.
    """
    compatibility = 1
    name = 'lxc'

    def __init__(self, parent, parameters):
        super(Lxc, self).__init__(parent)
        self.action = LxcAction()
        self.action.section = self.action_type
        self.action.job = self.job
        parent.add_action(self.action, parameters)

    @classmethod
    def accepts(cls, device, parameters):
        if 'to' not in parameters:
            return False, '"to" is not in deploy parameters'
        if 'os' not in parameters:
            return False, '"os" is not in deploy parameters'
        if parameters['to'] != 'lxc':
            return False, '"to" parameter is not "lxc"'
        if 'lxc' in device['actions']['deploy']['methods']:
            return True, 'accepted'
        return False, '"lxc" was not in the device configuration deploy methods'


class LxcAction(DeployAction):  # pylint:disable=too-many-instance-attributes

    def __init__(self):
        super(LxcAction, self).__init__()
        self.name = "lxc-deploy"
        self.description = "download files and deploy using lxc"
        self.summary = "lxc deployment"
        self.lxc_data = {}

    def validate(self):
        super(LxcAction, self).validate()
        self.logger.info("lxc, installed at version: %s" %
                         debian_package_version(pkg='lxc', split=False))
        protocols = [protocol.name for protocol in self.job.protocols]
        if LxcProtocol.name not in protocols:
            self.logger.debug("Missing protocol '%s' in %s",
                              LxcProtocol.name, protocols)
            self.errors = "Missing protocol '%s'" % LxcProtocol.name
        self.errors = infrastructure_error('lxc-create')
        if self.test_needs_deployment(self.parameters):
            lava_test_results_dir = self.parameters['deployment_data'][
                'lava_test_results_dir']
            lava_test_results_dir = lava_test_results_dir % self.job.job_id
            self.set_namespace_data(action='test', label='results',
                                    key='lava_test_results_dir',
                                    value=lava_test_results_dir)
            lava_test_sh_cmd = self.parameters['deployment_data'][
                'lava_test_sh_cmd']
            self.set_namespace_data(action=self.name, label='shared',
                                    key='lava_test_sh_cmd',
                                    value=lava_test_sh_cmd)

    def populate(self, parameters):
        self.internal_pipeline = Pipeline(parent=self, job=self.job,
                                          parameters=parameters)
        self.internal_pipeline.add_action(LxcCreateAction())
        self.internal_pipeline.add_action(LxcCreateUdevRuleAction())
        if 'packages' in parameters:
            self.internal_pipeline.add_action(LxcStartAction())
            self.internal_pipeline.add_action(LxcAptUpdateAction())
            self.internal_pipeline.add_action(LxcAptInstallAction())
            self.internal_pipeline.add_action(LxcStopAction())
        if self.test_needs_deployment(parameters):
            self.internal_pipeline.add_action(DeployDeviceEnvironment())
        if self.test_needs_overlay(parameters):
            self.internal_pipeline.add_action(OverlayAction())
            self.internal_pipeline.add_action(ApplyLxcOverlay())


class LxcCreateAction(DeployAction):
    """
    Creates Lxc container.
    """

    def __init__(self):
        super(LxcCreateAction, self).__init__()
        self.name = "lxc-create-action"
        self.description = "create lxc action"
        self.summary = "create lxc"
        self.retries = 10
        self.sleep = 10
        self.lxc_data = {}

    def _set_lxc_data(self):
        protocols = [protocol for protocol in self.job.protocols
                     if protocol.name == LxcProtocol.name]
        if protocols:
            protocol = protocols[0]
            self.set_namespace_data(action=self.name, label='lxc', key='name', value=protocol.lxc_name)
            self.lxc_data['lxc_name'] = protocol.lxc_name
            self.lxc_data['lxc_distribution'] = protocol.lxc_dist
            self.lxc_data['lxc_release'] = protocol.lxc_release
            self.lxc_data['lxc_arch'] = protocol.lxc_arch
            self.lxc_data['lxc_template'] = protocol.lxc_template
            self.lxc_data['lxc_mirror'] = protocol.lxc_mirror
            self.lxc_data['lxc_security_mirror'] = protocol.lxc_security_mirror
            self.lxc_data['verbose'] = protocol.verbose
            self.lxc_data['lxc_persist'] = protocol.persistence
            self.lxc_data['custom_lxc_path'] = protocol.custom_lxc_path

    def validate(self):
        super(LxcCreateAction, self).validate()
        # set lxc_data
        self._set_lxc_data()

    def run(self, connection, max_end_time, args=None):
        connection = super(LxcCreateAction, self).run(connection, max_end_time, args)
        verbose = '' if self.lxc_data['verbose'] else '-q'
        lxc_default_path = lxc_path(self.job.parameters['dispatcher'])
        if self.lxc_data['custom_lxc_path']:
            lxc_create = ['lxc-create', '-P', lxc_default_path]
        else:
            lxc_create = ['lxc-create']
        if self.lxc_data['lxc_template'] in LXC_TEMPLATE_WITH_MIRROR:
            lxc_cmd = lxc_create + [verbose, '-t',
                                    self.lxc_data['lxc_template'], '-n',
                                    self.lxc_data['lxc_name'], '--',
                                    '--release', self.lxc_data['lxc_release']]
            if self.lxc_data['lxc_mirror']:
                lxc_cmd += ['--mirror', self.lxc_data['lxc_mirror']]
            if self.lxc_data['lxc_security_mirror']:
                lxc_cmd += ['--security-mirror',
                            self.lxc_data['lxc_security_mirror']]
            # FIXME: Should be removed when LAVA's supported distro is bumped
            #        to Debian Stretch or any distro that supports systemd
            lxc_cmd += ['--packages', LXC_DEFAULT_PACKAGES]
        else:
            lxc_cmd = lxc_create + [verbose, '-t',
                                    self.lxc_data['lxc_template'], '-n',
                                    self.lxc_data['lxc_name'], '--', '--dist',
                                    self.lxc_data['lxc_distribution'],
                                    '--release', self.lxc_data['lxc_release']]
        if self.lxc_data['lxc_arch']:
            lxc_cmd += ['--arch', self.lxc_data['lxc_arch']]
        cmd_out = self.run_command(lxc_cmd, allow_fail=True, allow_silent=True)
        if isinstance(cmd_out, str):
            if 'exists' in cmd_out and self.lxc_data['lxc_persist']:
                self.logger.debug('Persistant container exists')
                self.results = {'status': self.lxc_data['lxc_name']}
        elif not cmd_out:
            raise JobError("Unable to create lxc container")
        else:
            self.logger.debug('Container created successfully')
            self.results = {'status': self.lxc_data['lxc_name']}
        # Create symlink in default container path ie., /var/lib/lxc defined by
        # LXC_PATH so that we need not add '-P' option to every lxc-* command.
        dst = os.path.join(LXC_PATH, self.lxc_data['lxc_name'])
        if self.lxc_data['custom_lxc_path'] and not os.path.exists(dst):
            os.symlink(os.path.join(lxc_default_path,
                                    self.lxc_data['lxc_name']),
                       os.path.join(LXC_PATH,
                                    self.lxc_data['lxc_name']))
        return connection


class LxcCreateUdevRuleAction(DeployAction):
    """
    Creates Lxc related udev rules for this container.
    """

    def __init__(self):
        super(LxcCreateUdevRuleAction, self).__init__()
        self.name = "lxc-create-udev-rule-action"
        self.description = "create lxc udev rule action"
        self.summary = "create lxc udev rule"
        self.retries = 10
        self.sleep = 10

    def validate(self):
        super(LxcCreateUdevRuleAction, self).validate()
        self.errors = infrastructure_error('udevadm')
        if 'device_info' in self.job.device \
           and not isinstance(self.job.device.get('device_info'), list):
            self.errors = "device_info unset"
        try:
            if 'device_info' in self.job.device:
                for usb_device in self.job.device['device_info']:
                    if usb_device.get('board_id', '') in ['', '0000000000']:
                        self.errors = "board_id unset"
                    if usb_device.get('usb_vendor_id', '') == '0000':
                        self.errors = 'usb_vendor_id unset'
                    if usb_device.get('usb_product_id', '') == '0000':
                        self.errors = 'usb_product_id unset'
        except TypeError:
            self.errors = "Invalid parameters for %s" % self.name

    def run(self, connection, max_end_time, args=None):
        connection = super(LxcCreateUdevRuleAction, self).run(connection,
                                                              max_end_time,
                                                              args)
        # this may be the device namespace - the lxc namespace may not be
        # accessible
        lxc_name = None
        protocols = [protocol for protocol in self.job.protocols if protocol.name == LxcProtocol.name]
        if protocols:
            lxc_name = protocols[0].lxc_name
        if not lxc_name:
            self.logger.debug("No LXC device requested")
            return connection

        # If there is no device_info then this action should be idempotent.
        if 'device_info' not in self.job.device:
            return connection

        device_info = self.job.device.get('device_info', [])
        device_info_file = os.path.join(self.mkdtemp(), 'device-info.yaml')
        with open(device_info_file, 'w') as device_info_obj:
            device_info_obj.write(str(device_info))
        self.logger.debug("device info file '%s' created with:\n %s",
                          device_info_file, device_info)
        logging_url = master_cert = slave_cert = ipv6 = None
        job_id = self.job.job_id
        if self.logger.handler:
            logging_url = self.logger.handler.logging_url
            master_cert = self.logger.handler.master_cert
            slave_cert = self.logger.handler.slave_cert
            ipv6 = self.logger.handler.ipv6
            job_id = self.logger.handler.job_id
        for device in device_info:
            data = {'serial_number': str(device.get('board_id', '')),
                    'vendor_id': device.get('usb_vendor_id', None),
                    'product_id': device.get('usb_product_id', None),
                    'lxc_name': lxc_name,
                    'device_info_file': device_info_file,
                    'logging_url': logging_url,
                    'master_cert': master_cert,
                    'slave_cert': slave_cert,
                    'ipv6': ipv6,
                    'job_id': job_id}
            # The rules file will be something like
            # /etc/udev/rules.d/100-lxc-hikey-2808.rules'
            # where, 100 is just an arbitrary number which specifies loading
            # priority for udevd
            rules_file = os.path.join(UDEV_RULES_DIR,
                                      '100-' + lxc_name + '.rules')
            str_lxc_udev_rule = lxc_udev_rule(data)
            with open(rules_file, 'a') as f_obj:
                f_obj.write(str_lxc_udev_rule)
            self.logger.debug("udev rules file '%s' created with:\n %s",
                              rules_file, str_lxc_udev_rule)

        # Reload udev rules.
        reload_cmd = ['udevadm', 'control', '--reload-rules']
        cmd_out = self.run_command(reload_cmd, allow_fail=True,
                                   allow_silent=True)
        if cmd_out is False:
            self.logger.debug('Reloading udev rules failed')
        else:
            self.logger.debug('udev rules reloaded.')
        return connection


class LxcAptUpdateAction(DeployAction):
    """
    apt-get update the lxc container.
    """

    def __init__(self):
        super(LxcAptUpdateAction, self).__init__()
        self.name = "lxc-apt-update"
        self.description = "lxc apt update action"
        self.summary = "lxc apt update"
        self.retries = 10
        self.sleep = 10

    def validate(self):
        super(LxcAptUpdateAction, self).validate()

    def run(self, connection, max_end_time, args=None):
        connection = super(LxcAptUpdateAction, self).run(connection,
                                                         max_end_time, args)
        lxc_name = self.get_namespace_data(action='lxc-create-action',
                                           label='lxc', key='name')
        cmd = ['lxc-attach', '-n', lxc_name, '--', 'apt-get', '-y', 'update']
        if not self.run_command(cmd, allow_silent=True):
            raise JobError("Unable to apt-get update in lxc container")
        return connection


class LxcAptInstallAction(DeployAction):
    """
    apt-get install packages to the lxc container.
    """

    def __init__(self):
        super(LxcAptInstallAction, self).__init__()
        self.name = "lxc-apt-install"
        self.description = "lxc apt install packages action"
        self.summary = "lxc apt install"
        self.retries = 10
        self.sleep = 10

    def validate(self):
        super(LxcAptInstallAction, self).validate()
        if 'packages' not in self.parameters:
            raise LAVABug("%s package list unavailable" % self.name)

    def run(self, connection, max_end_time, args=None):
        connection = super(LxcAptInstallAction, self).run(connection,
                                                          max_end_time, args)
        lxc_name = self.get_namespace_data(action='lxc-create-action',
                                           label='lxc', key='name')
        packages = self.parameters['packages']
        cmd = ['lxc-attach', '-n', lxc_name, '--', 'apt-get', '-y',
               'install'] + packages
        if not self.run_command(cmd):
            raise JobError("Unable to install using apt-get in lxc container")
        return connection
