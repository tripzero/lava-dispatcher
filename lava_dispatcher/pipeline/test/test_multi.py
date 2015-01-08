# Copyright (C) 2014 Linaro Limited
#
# Author: Neil Williams <neil.williams@linaro.org>
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


import unittest
from lava_dispatcher.pipeline.job import Job, ResetContext
from lava_dispatcher.pipeline.action import Pipeline
from lava_dispatcher.pipeline.actions.deploy import DeployAction
from lava_dispatcher.pipeline.actions.boot import BootAction
from lava_dispatcher.pipeline.device import NewDevice
from lava_dispatcher.pipeline.power import FinalizeAction
from lava_dispatcher.pipeline.utils.filesystem import mkdtemp
from lava_dispatcher.pipeline.parser import handle_device_parameters
from lava_dispatcher.pipeline.test.test_uboot import Factory


class TestMultiDeploy(unittest.TestCase):  # pylint: disable=too-many-public-methods

    def setUp(self):
        self.parameters = {}
        self.parsed_data = {  # fake parsed YAML
            'device_type': 'fake',
            'job_name': 'fake_job',
            'timeouts': {
                'job': {
                    'minutes': 2
                }
            },
            'priority': 'medium',
            'actions': [
                {
                    'deploy': {
                        'to': 'fake_to',
                        'example': 'nowhere'
                    }
                },
                {
                    'deploy': {
                        'to': 'destination',
                        'parameters': 'faked'
                    }
                },
                {
                    'deploy': {
                        'to': 'tftp',
                        'parameters': 'valid'
                    }
                }
            ]
        }

    class FakeDevice(NewDevice):
        def __init__(self):
            super(TestMultiDeploy.FakeDevice, self).__init__('bbb-01')

    class TestDeploy(object):  # cannot be a subclass of Deployment without a full select function.
        def __init__(self, parent, parameters, job):
            super(TestMultiDeploy.TestDeploy, self).__init__()
            self.action = TestMultiDeploy.TestDeployAction()
            self.action.job = job
            parent.add_action(self.action, parameters)

    class TestDeployAction(DeployAction):

        def __init__(self):
            super(TestMultiDeploy.TestDeployAction, self).__init__()
            self.name = "fake_deploy"
            self.summary = "fake deployment"
            self.description = "fake for tests only"

        def validate(self):
            super(TestMultiDeploy.TestDeployAction, self).validate()

        def run(self, connection, args=None):
            self.data[self.name] = self.parameters
            return connection  # no actual connection during this fake job

    class TestJob(Job):
        def __init__(self):
            super(TestMultiDeploy.TestJob, self).__init__(self.parameters)

    def test_multi_deploy(self):
        self.assertIsNotNone(self.parsed_data)
        job = Job(self.parsed_data)
        pipeline = Pipeline(job=job)
        device = TestMultiDeploy.FakeDevice()
        self.assertIsNotNone(device)
        job.device = device
        job.parameters['output_dir'] = mkdtemp()
        job.set_pipeline(pipeline)
        counts = {}
        for action_data in self.parsed_data['actions']:
            for name in action_data:
                counts.setdefault(name, 1)
                if counts[name] >= 2:
                    pipeline.add_action(ResetContext())
                parameters = handle_device_parameters(action_data[name], name, device.parameters)
                parameters.update(action_data[name])
                test_deploy = TestMultiDeploy.TestDeploy(pipeline, parameters, job)
                self.assertEqual(
                    {'common': {}},
                    test_deploy.action.data
                )
                counts[name] += 1
        # check that only one action has the example set
        self.assertEqual(
            ['nowhere'],
            [detail['deploy']['example'] for detail in self.parsed_data['actions'] if 'example' in detail['deploy']]
        )
        self.assertEqual(
            ['faked', 'valid'],
            [detail['deploy']['parameters'] for detail in self.parsed_data['actions'] if 'parameters' in detail['deploy']]
        )
        self.assertIsInstance(pipeline.actions[0], TestMultiDeploy.TestDeployAction)
        self.assertIsInstance(pipeline.actions[1], ResetContext)
        self.assertIsInstance(pipeline.actions[2], TestMultiDeploy.TestDeployAction)
        self.assertIsInstance(pipeline.actions[3], ResetContext)
        self.assertIsInstance(pipeline.actions[4], TestMultiDeploy.TestDeployAction)
        job.validate()
        self.assertEqual([], job.pipeline.errors)
        job.run()
        self.assertNotEqual(pipeline.actions[0].data, {'common': {}, 'fake_deploy': pipeline.actions[0].parameters})
        self.assertNotEqual(pipeline.actions[1].data, {'common': {}, 'fake_deploy': pipeline.actions[1].parameters})
        self.assertEqual(pipeline.actions[2].data, {'common': {}, 'fake_deploy': pipeline.actions[4].parameters})
        # check that values from previous DeployAction run actions have been cleared
        self.assertEqual(pipeline.actions[4].data, {'common': {}, 'fake_deploy': pipeline.actions[4].parameters})


class TestMultiUBoot(unittest.TestCase):  # pylint: disable=too-many-public-methods

    def setUp(self):
        factory = Factory()
        self.job = factory.create_bbb_job('sample_jobs/uboot-multiple.yaml')
        self.assertIsNotNone(self.job)
        self.assertIsNone(self.job.validate())
        self.assertEqual(self.job.device.parameters['device_type'], 'beaglebone-black')

    def test_multi_uboot(self):
        self.assertIsNotNone(self.job)
        self.assertIsInstance(self.job.pipeline.actions[0], DeployAction)
        self.assertIsInstance(self.job.pipeline.actions[1], BootAction)
        self.assertIsInstance(self.job.pipeline.actions[2], ResetContext)
        self.assertIsInstance(self.job.pipeline.actions[3], DeployAction)
        self.assertIsInstance(self.job.pipeline.actions[4], BootAction)
        self.assertIsInstance(self.job.pipeline.actions[5], FinalizeAction)
