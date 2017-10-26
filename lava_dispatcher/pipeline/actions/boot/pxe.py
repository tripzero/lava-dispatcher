# Copyright (C) 2014 Linaro Limited
#
# Author: Matthew Hart <matthew.hart@linaro.org>
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

# List just the subclasses supported for this base strategy
# imported by the parser to populate the list of subclasses.

from lava_dispatcher.pipeline.action import (
    Action,
    Pipeline,
)
from lava_dispatcher.pipeline.logical import Boot
from lava_dispatcher.pipeline.actions.boot import (
    BootAction,
    AutoLoginAction,
    OverlayUnpack,
)
from lava_dispatcher.pipeline.actions.boot.environment import ExportDeviceEnvironment
from lava_dispatcher.pipeline.shell import ExpectShellSession
from lava_dispatcher.pipeline.connections.serial import ConnectDevice
from lava_dispatcher.pipeline.power import ResetDevice


class PXE(Boot):
    """
    The PXE method prepares the command to run on the dispatcher but this
    command needs to start a new connection and then interrupt iPXE.
    An expect shell session can then be handed over to the BootloaderAction.
    self.run_command is a blocking call, so Boot needs to use
    a direct spawn call via ShellCommand (which wraps pexpect.spawn) then
    hand this pexpect wrapper to subsequent actions as a shell connection.
    """

    compatibility = 1

    def __init__(self, parent, parameters):
        super(PXE, self).__init__(parent)
        self.action = DispatchAction()
        self.action.section = self.action_type
        self.action.job = self.job
        parent.add_action(self.action, parameters)

    @classmethod
    def accepts(cls, device, parameters):
        if parameters['method'] != 'pxe':
            return False, '"method" was not "pxe"'
        if 'pxe' in device['actions']['boot']['methods']:
            return True, 'accepted'
        else:
            return False, '"pxe" was not in the device configuration boot methods'

class DispatchAction(BootAction):
    """
    Wait's for "started flasher.sh kernel message and launches dispatcher"
    """
    def __init(self):
        super(DispatchAction,  self).__init__()
        self.name = "dispatch-action"
        self.description = "image flashing action"
        self.summary = "dispatch image to device"
        
    def populate(self,  parameters):
        self.internal_pipeline = Pipeline(parent=self,  job=self.job,  parameters=parameters)
        # customize the device configuration for this job
        self.internal_pipeline.add_action(ConnectDevice())
        self.internal_pipeline.add_action(ResetDevice())
        self.internal_pipeline.add_action(RunDispatcher())
        # reboot to the newly flashed image
        self.internal_pipeline.add_action(ResetDevice())
        if self.has_prompts(parameters):
            self.internal_pipeline.add_action(AutoLoginAction())
            if self.test_has_shell(parameters):
                self.internal_pipeline.add_action(ExpectShellSession())
                if 'transfer_overlay' in parameters:
                    self.internal_pipeline.add_action(OverlayUnpack())
                self.internal_pipeline.add_action(ExportDeviceEnvironment())


class RunDispatcher(Action):
    """
    Runs the dispatcher process
    """
    def __init__(self):
        super(RunDispatcher,  self).__init__()
        self.name = "run-dispatcher-action"
        self.description = "run dispatcher process"
        self.summary = "runs the dispatcher process"

    def validate(self):
        params = self.job.device['actions']['boot']['methods'][self.type]['parameters']
        if not "dispatcher_cmd" in params:
            self.errors = "missing dispatcher_cmd for device"

    def run(self, connection, max_end_time, args=None):
        params = self.job.device['actions']['boot']['methods'][self.type]['parameters']
        dispatcher_cmd = params["dispatcher_cmd"]
        
        if not connection:
            self.logger.debug("%s started without a connection already in use" % self.name)
            raise Exception("%s started without a connection already in use" % self.name)
        connection = super(RunDispatcher, self).run(connection, max_end_time, args)
        connection.prompt_str = "Started lava-flasher.service."
        self.wait(connection)
        self.run_command(dispatcher_cmd)
        return connection

