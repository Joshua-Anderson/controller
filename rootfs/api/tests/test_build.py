"""
Unit tests for the Deis api app.

Run the tests with "./manage.py test api"
"""
import json

from django.contrib.auth.models import User
from django.core.cache import cache
from django.conf import settings
from unittest import mock
from rest_framework.authtoken.models import Token

from api.models import Build
from registry.dockerclient import RegistryException
from scheduler import KubeException

from api.tests import adapter, mock_port, DeisTransactionTestCase
import requests_mock


@requests_mock.Mocker(real_http=True, adapter=adapter)
@mock.patch('api.models.release.publish_release', lambda *args: None)
@mock.patch('api.models.release.docker_get_port', mock_port)
class BuildTest(DeisTransactionTestCase):

    """Tests build notification from build system"""

    fixtures = ['tests.json']

    def setUp(self):
        self.user = User.objects.get(username='autotest')
        self.token = Token.objects.get(user=self.user).key
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token)

    def tearDown(self):
        # make sure every test has a clean slate for k8s mocking
        cache.clear()

    def test_build(self, mock_requests):
        """
        Test that a null build is created and that users can post new builds
        """
        app_id = self.create_app()

        # check to see that no initial build was created
        url = "/v2/apps/{app_id}/builds".format(**locals())
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['count'], 0)

        # post a new build
        body = {'image': 'autotest/example'}
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 201, response.data)
        build_id = str(response.data['uuid'])
        build1 = response.data
        self.assertEqual(response.data['image'], body['image'])

        # read the build
        url = "/v2/apps/{app_id}/builds/{build_id}".format(**locals())
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        build2 = response.data
        self.assertEqual(build1, build2)

        # post a new build
        url = "/v2/apps/{app_id}/builds".format(**locals())
        body = {'image': 'autotest/example'}
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 201, response.data)
        build3 = response.data
        self.assertEqual(response.data['image'], body['image'])
        self.assertNotEqual(build2['uuid'], build3['uuid'])

        # disallow put/patch/delete
        response = self.client.put(url)
        self.assertEqual(response.status_code, 405, response.content)
        response = self.client.patch(url)
        self.assertEqual(response.status_code, 405, response.content)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 405, response.content)

    def test_response_data(self, mock_requests):
        """Test that the serialized response contains only relevant data."""
        app_id = self.create_app()

        # post an image as a build
        url = "/v2/apps/{app_id}/builds".format(**locals())
        body = {'image': 'autotest/example'}
        response = self.client.post(url, body)

        for key in response.data:
            self.assertIn(key, ['uuid', 'owner', 'created', 'updated', 'app', 'dockerfile',
                                'image', 'procfile', 'sha'])
        expected = {
            'owner': self.user.username,
            'app': app_id,
            'dockerfile': '',
            'image': 'autotest/example',
            'procfile': {},
            'sha': ''
        }
        self.assertDictContainsSubset(expected, response.data)

    def test_build_default_containers(self, mock_requests):
        app_id = self.create_app()

        # post an image as a build
        url = "/v2/apps/{app_id}/builds".format(**locals())
        body = {'image': 'autotest/example'}
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 201, response.data)

        url = "/v2/apps/{app_id}/pods/cmd".format(**locals())
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['results']), 1)
        container = response.data['results'][0]
        self.assertEqual(container['type'], 'cmd')
        self.assertEqual(container['release'], 'v2')
        # pod name is auto generated so use regex
        self.assertRegex(container['name'], app_id + '-cmd-[0-9]{8,10}-[a-z0-9]{5}')

        # start with a new app
        app_id = self.create_app()
        # post a new build with procfile
        url = "/v2/apps/{app_id}/builds".format(**locals())
        body = {
            'image': 'autotest/example',
            'sha': 'a'*40,
            'dockerfile': "FROM scratch"
        }
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 201, response.data)

        url = "/v2/apps/{app_id}/pods/cmd".format(**locals())
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['results']), 1)
        container = response.data['results'][0]
        self.assertEqual(container['type'], 'cmd')
        self.assertEqual(container['release'], 'v2')
        # pod name is auto generated so use regex
        self.assertRegex(container['name'], app_id + '-cmd-[0-9]{8,10}-[a-z0-9]{5}')

        # start with a new app
        app_id = self.create_app()

        # post a new build with procfile
        url = "/v2/apps/{app_id}/builds".format(**locals())
        body = {
            'image': 'autotest/example',
            'sha': 'a'*40,
            'dockerfile': "FROM scratch",
            'procfile': {
                'worker': 'node worker.js'
            }
        }
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 201, response.data)

        url = "/v2/apps/{app_id}/pods/cmd".format(**locals())
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['results']), 1)
        container = response.data['results'][0]
        self.assertEqual(container['type'], 'cmd')
        self.assertEqual(container['release'], 'v2')
        # pod name is auto generated so use regex
        self.assertRegex(container['name'], app_id + '-cmd-[0-9]{8,10}-[a-z0-9]{5}')

        # start with a new app
        app_id = self.create_app()
        # post a new build with procfile

        url = "/v2/apps/{app_id}/builds".format(**locals())
        body = {
            'image': 'autotest/example',
            'sha': 'a'*40,
            'procfile': json.dumps({
                'web': 'node server.js',
                'worker': 'node worker.js'
            })
        }
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 201, response.data)

        url = "/v2/apps/{app_id}/pods/web".format(**locals())
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['results']), 1)
        container = response.data['results'][0]
        self.assertEqual(container['type'], 'web')
        self.assertEqual(container['release'], 'v2')
        # pod name is auto generated so use regex
        self.assertRegex(container['name'], app_id + '-web-[0-9]{8,10}-[a-z0-9]{5}')

        # start with a new app
        app_id = self.create_app()
        # post a new build with procfile and no routable type

        url = "/v2/apps/{app_id}/builds".format(**locals())
        body = {
            'image': 'autotest/example',
            'sha': 'a'*40,
            'procfile': json.dumps({
                'rake': 'node server.js',
                'worker': 'node worker.js'
            })
        }
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 201, response.data)

    def test_build_str(self, mock_requests):
        """Test the text representation of a build."""
        app_id = self.create_app()

        # post a new build
        url = "/v2/apps/{app_id}/builds".format(**locals())
        body = {'image': 'autotest/example'}
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 201, response.data)
        build = Build.objects.get(uuid=response.data['uuid'])
        self.assertEqual(str(build), "{}-{}".format(
                         response.data['app'], str(response.data['uuid'])[:7]))

    def test_admin_can_create_builds_on_other_apps(self, mock_requests):
        """If a user creates an application, an administrator should be able
        to push builds.
        """
        # create app as non-admin
        user = User.objects.get(username='autotest2')
        token = Token.objects.get(user=user).key
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token)

        app_id = self.create_app()

        # post a new build as admin
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token)
        url = "/v2/apps/{app_id}/builds".format(**locals())
        body = {'image': 'autotest/example'}
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 201, response.data)

        build = Build.objects.get(uuid=response.data['uuid'])
        self.assertEqual(str(build), "{}-{}".format(
                         response.data['app'], str(response.data['uuid'])[:7]))

    def test_unauthorized_user_cannot_modify_build(self, mock_requests):
        """
        An unauthorized user should not be able to modify other builds.

        Since an unauthorized user can't access the application, these
        requests should return a 403.
        """
        app_id = self.create_app()

        unauthorized_user = User.objects.get(username='autotest2')
        unauthorized_token = Token.objects.get(user=unauthorized_user).key
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + unauthorized_token)
        url = '/v2/apps/{}/builds'.format(app_id)
        body = {'image': 'foo'}
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 403)

    def test_new_build_does_not_scale_up_automatically(self, mock_requests):
        """
        After the first initial deploy, if the containers are scaled down to zero,
        they should stay that way on a new release.
        """
        app_id = self.create_app()

        # post a new build
        url = "/v2/apps/{app_id}/builds".format(**locals())
        body = {
            'image': 'autotest/example',
            'sha': 'a'*40,
            'procfile': json.dumps({
                'web': 'node server.js',
                'worker': 'node worker.js'
            })
        }
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 201, response.data)

        url = "/v2/apps/{app_id}/pods/web".format(**locals())
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['results']), 1)

        # scale to zero
        url = "/v2/apps/{app_id}/scale".format(**locals())
        body = {'web': 0}
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 204, response.data)

        # post another build
        url = "/v2/apps/{app_id}/builds".format(**locals())
        body = {
            'image': 'autotest/example',
            'sha': 'a'*40,
            'procfile': json.dumps({
                'web': 'node server.js',
                'worker': 'node worker.js'
            })
        }
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 201, response.data)
        url = "/v2/apps/{app_id}/pods/web".format(**locals())
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['results']), 0)

    def test_build_image_in_registry(self, mock_requests):
        """When the image is already in the deis registry no pull/tag/push happens"""
        app_id = self.create_app()

        # post an image as a build using registry hostname
        url = "/v2/apps/{app_id}/builds".format(**locals())
        image = '{}/autotest/example'.format(settings.REGISTRY_HOST)
        body = {'image': image}
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 201, response.data)

        build = Build.objects.get(uuid=response.data['uuid'])
        release = build.app.release_set.latest()
        self.assertEqual(release.image, image)

        # post an image as a build using registry hostname + port
        url = "/v2/apps/{app_id}/builds".format(**locals())
        image = '{}/autotest/example'.format(settings.REGISTRY_URL)
        body = {'image': image}
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 201, response.data)

        build = Build.objects.get(uuid=response.data['uuid'])
        release = build.app.release_set.latest()
        self.assertEqual(release.image, image)

    def test_build_image_in_registry_with_auth(self, mock_requests):
        """add authentication to the build"""
        app_id = self.create_app()

        # post an image as a build using registry hostname
        url = "/v2/apps/{app_id}/builds".format(**locals())
        image = 'autotest/example'
        response = self.client.post(url, {'image': image})
        self.assertEqual(response.status_code, 201, response.data)

        # add the required PORT information
        url = '/v2/apps/{app_id}/config'.format(**locals())
        body = {'values': json.dumps({'PORT': '80'})}
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 201, response.data)

        # set some registry information
        url = '/v2/apps/{app_id}/config'.format(**locals())
        body = {'registry': json.dumps({'username': 'bob', 'password': 'zoomzoom'})}
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 201, response.data)

    def test_build_image_in_registry_with_auth_no_port(self, mock_requests):
        """add authentication to the build but with no PORT config"""
        app_id = self.create_app()

        # post an image as a build using registry hostname
        url = "/v2/apps/{app_id}/builds".format(**locals())
        image = 'autotest/example'
        response = self.client.post(url, {'image': image})
        self.assertEqual(response.status_code, 201, response.data)

        # set some registry information
        url = '/v2/apps/{app_id}/config'.format(**locals())
        body = {'registry': json.dumps({'username': 'bob', 'password': 'zoomzoom'})}
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 400, response.data)

    def test_release_create_failure(self, mock_requests):
        """
        Cause an Exception in app.deploy to cause a release.delete in build.create
        """
        app_id = self.create_app()

        # deploy app to get a build
        url = "/v2/apps/{app_id}/builds".format(**locals())
        body = {'image': 'autotest/example'}
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['image'], body['image'])

        with mock.patch('api.models.App.deploy') as mock_deploy:
            mock_deploy.side_effect = Exception('Boom!')

            url = "/v2/apps/{app_id}/builds".format(**locals())
            body = {'image': 'autotest/example'}
            response = self.client.post(url, body)
            self.assertEqual(response.status_code, 400, response.data)

    def test_release_registry_create_failure(self, mock_requests):
        """
        Cause a RegistryException in app.deploy to cause a release.delete in build.create
        """
        app_id = self.create_app()

        # deploy app to get a build
        url = "/v2/apps/{app_id}/builds".format(**locals())
        body = {'image': 'autotest/example'}
        response = self.client.post(url, body)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['image'], body['image'])

        with mock.patch('api.models.Release.publish') as mock_registry:
            mock_registry.side_effect = RegistryException('Boom!')

            url = "/v2/apps/{app_id}/builds".format(**locals())
            body = {'image': 'autotest/example'}
            response = self.client.post(url, body)
            self.assertEqual(response.status_code, 400, response.data)

    def test_build_deploy_kube_failure(self, mock_requests):
        """
        Cause an Exception in scheduler.deploy
        """
        app_id = self.create_app()

        with mock.patch('scheduler.KubeHTTPClient.deploy') as mock_deploy:
            mock_deploy.side_effect = KubeException('Boom!')

            url = "/v2/apps/{app_id}/builds".format(**locals())
            body = {'image': 'autotest/example'}
            response = self.client.post(url, body)
            self.assertEqual(response.status_code, 400, response.data)
