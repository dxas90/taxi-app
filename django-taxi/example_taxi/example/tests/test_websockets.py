# example/tests/test_websockets.py
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from nose.tools import assert_true, assert_is_not_none, assert_equal, assert_is_none
import pytest

from example.models import Trip
from example_taxi.routing import application


TEST_CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}


@database_sync_to_async
def create_user(
    *,
    username='rider@example.com',
    password='pAssw0rd!',
    group='rider'
):
    # Create user.
    user = get_user_model().objects.create_user(
        username=username,
        password=password
    )

    # Create user group.
    user_group, _ = Group.objects.get_or_create(name=group)
    user.groups.add(user_group)
    user.save()
    return user


async def auth_connect(user):
    client = Client()
    client.force_login(user=user)
    communicator = WebsocketCommunicator(
        application=application,
        path='/taxi/',
        headers=[(
            b'cookie',
            f'sessionid={client.cookies["sessionid"].value}'.encode('ascii')
        )]
    )
    connected, _ = await communicator.connect()
    assert_true(connected)
    return communicator


async def connect_and_create_trip(
    *,
    user,
    pick_up_address='A',
    drop_off_address='B'
):
    communicator = await auth_connect(user)
    await communicator.send_json_to({
        'type': 'create.trip',
        'data': {
            'pick_up_address': pick_up_address,
            'drop_off_address': drop_off_address,
            'rider': user.id,
        }
    })
    return communicator


@database_sync_to_async
def create_trip(**kwargs):
    return Trip.objects.create(**kwargs)


async def connect_and_update_trip(*, user, trip, status):
    communicator = await auth_connect(user)
    await communicator.send_json_to({
        'type': 'update.trip',
        'data': {
            'id': trip.id,
            'nk': trip.nk,
            'pick_up_address': trip.pick_up_address,
            'drop_off_address': trip.drop_off_address,
            'status': status,
            'driver': user.id,
        }
    })
    return communicator


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestWebsockets:

    async def test_authorized_user_can_connect(self, settings):
        # Use in-memory channel layers for testing.
        settings.CHANNEL_LAYERS = TEST_CHANNEL_LAYERS

        user = await create_user(
            username='rider@example.com',
            group='rider'
        )
        communicator = await auth_connect(user)
        await communicator.disconnect()

    async def test_rider_can_create_trips(self, settings):
        settings.CHANNEL_LAYERS = TEST_CHANNEL_LAYERS

        user = await create_user(
            username='rider@example.com',
            group='rider'
        )
        communicator = await connect_and_create_trip(user=user)

        # Receive JSON message from server.
        response = await communicator.receive_json_from()
        data = response.get('data')

        # Confirm data.
        assert_is_not_none(data['id'])
        assert_is_not_none(data['nk'])
        assert_equal('A', data['pick_up_address'])
        assert_equal('B', data['drop_off_address'])
        assert_equal(Trip.REQUESTED, data['status'])
        assert_is_none(data['driver'])
        assert_equal(user.username, data['rider'].get('username'))

        await communicator.disconnect()

    async def test_rider_is_added_to_trip_group_on_create(self, settings):
        settings.CHANNEL_LAYERS = TEST_CHANNEL_LAYERS

        user = await create_user(
            username='rider@example.com',
            group='rider'
        )

        # Connect and send JSON message to server.
        communicator = await connect_and_create_trip(user=user)

        # Receive JSON message from server.
        # Rider should be added to new trip's group.
        response = await communicator.receive_json_from()
        data = response.get('data')

        trip_nk = data['nk']
        message = {
            'type': 'echo.message',
            'data': 'This is a test message.'
        }

        # Send JSON message to new trip's group.
        channel_layer = get_channel_layer()
        await channel_layer.group_send(trip_nk, message=message)

        # Receive JSON message from server.
        response = await communicator.receive_json_from()

        # Confirm data.
        assert_equal(message, response)

        await communicator.disconnect()

    async def test_rider_is_added_to_trip_groups_on_connect(self, settings):
        settings.CHANNEL_LAYERS = TEST_CHANNEL_LAYERS

        user = await create_user(
            username='rider3@example.com',
            group='rider'
        )

        # Create trips and link to rider.
        trip = await create_trip(
            pick_up_address='A',
            drop_off_address='B',
            rider=user
        )

        # Connect to server.
        # Trips for rider should be retrieved.
        # Rider should be added to trips' groups.
        communicator = await auth_connect(user)

        message = {
            'type': 'echo.message',
            'data': 'This is a test message.'
        }

        channel_layer = get_channel_layer()

        # Test sending JSON message to trip group.
        await channel_layer.group_send(trip.nk, message=message)
        response = await communicator.receive_json_from()
        assert_equal(message, response)

        await communicator.disconnect()

    async def test_driver_can_update_trips(self, settings):
        settings.CHANNEL_LAYERS = TEST_CHANNEL_LAYERS

        trip = await create_trip(
            pick_up_address='A',
            drop_off_address='B'
        )
        user = await create_user(
            username='driver@example.com',
            group='driver'
        )

        # Send JSON message to server.
        communicator = await connect_and_update_trip(
            user=user,
            trip=trip,
            status=Trip.IN_PROGRESS
        )

        # Receive JSON message from server.
        response = await communicator.receive_json_from()
        data = response.get('data')

        # Confirm data.
        assert_equal(trip.id, data['id'])
        assert_equal(trip.nk, data['nk'])
        assert_equal('A', data['pick_up_address'])
        assert_equal('B', data['drop_off_address'])
        assert_equal(Trip.IN_PROGRESS, data['status'])
        assert_equal(user.username, data['driver'].get('username'))
        assert_equal(None, data['rider'])

        await communicator.disconnect()

    async def test_driver_is_added_to_trip_group_on_update(self, settings):
        settings.CHANNEL_LAYERS = TEST_CHANNEL_LAYERS

        trip = await create_trip(
            pick_up_address='A',
            drop_off_address='B'
        )
        user = await create_user(
            username='driver@example.com',
            group='driver'
        )

        # Send JSON message to server.
        communicator = await connect_and_update_trip(
            user=user,
            trip=trip,
            status=Trip.IN_PROGRESS
        )

        # Receive JSON message from server.
        response = await communicator.receive_json_from()
        data = response.get('data')

        trip_nk = data['nk']
        message = {
            'type': 'echo.message',
            'data': 'This is a test message.'
        }

        # Send JSON message to trip's group.
        channel_layer = get_channel_layer()
        await channel_layer.group_send(trip_nk, message=message)

        # Receive JSON message from server.
        response = await communicator.receive_json_from()

        # Confirm data.
        assert_equal(message, response)

        await communicator.disconnect()

    async def test_driver_is_alerted_on_trip_create(self, settings):
        settings.CHANNEL_LAYERS = TEST_CHANNEL_LAYERS

        # Listen to the 'drivers' group test channel.
        channel_layer = get_channel_layer()
        await channel_layer.group_add(
            group='drivers',
            channel='test_channel'
        )

        user = await create_user(
            username='rider@example.com',
            group='rider'
        )

        # Send JSON message to server.
        communicator = await connect_and_create_trip(user=user)

        # Receive JSON message from server on test channel.
        response = await channel_layer.receive('test_channel')
        data = response.get('data')

        # Confirm data.
        assert_is_not_none(data['nk'])
        assert_equal(user.username, data['rider'].get('username'))

        await communicator.disconnect()

    async def test_rider_is_alerted_on_trip_update(self, settings):
        settings.CHANNEL_LAYERS = TEST_CHANNEL_LAYERS

        trip = await create_trip(
            pick_up_address='A',
            drop_off_address='B'
        )

        # Listen to the trip group test channel.
        channel_layer = get_channel_layer()
        await channel_layer.group_add(
            group=trip.nk,
            channel='test_channel'
        )

        user = await create_user(
            username='driver@example.com',
            group='driver'
        )

        # Send JSON message to server.
        communicator = await connect_and_update_trip(
            user=user,
            trip=trip,
            status=Trip.IN_PROGRESS
        )

        # Receive JSON message from server on test channel.
        response = await channel_layer.receive('test_channel')
        data = response.get('data')

        # Confirm data.
        assert_equal(trip.nk, data['nk'])
        assert_equal(user.username, data['driver'].get('username'))

        await communicator.disconnect()
