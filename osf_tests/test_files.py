import pytest

from django.contrib.contenttypes.models import ContentType

from addons.osfstorage.models import NodeSettings
from addons.osfstorage import settings as osfstorage_settings
from osf.models import BaseFileNode, Folder, File
from osf_tests.factories import (
    UserFactory,
    ProjectFactory,
    RegionFactory
)

pytestmark = pytest.mark.django_db

@pytest.fixture()
def user():
    return UserFactory()

@pytest.fixture()
def project(user):
    return ProjectFactory(creator=user)


@pytest.fixture()
def create_test_file(fake):
    # TODO: Copied from api_tests/utils.py. DRY this up.
    def _create_test_file(target, user=None, filename=None, create_guid=True):
        filename = filename or fake.file_name()
        user = user or target.creator
        osfstorage = target.get_addon('osfstorage')
        root_node = osfstorage.get_root()
        test_file = root_node.append_file(filename)

        if create_guid:
            test_file.get_guid(create=True)

        test_file.create_version(user, {
            'object': '06d80e',
            'service': 'cloud',
            osfstorage_settings.WATERBUTLER_RESOURCE: 'osf',
        }, {
            'size': 1337,
            'contentType': 'img/png'
        }).save()
        return test_file
    return _create_test_file


def test_active_manager_does_not_return_trashed_file_nodes(project, create_test_file):
    create_test_file(target=project)
    deleted_file = create_test_file(target=project)
    deleted_file.delete(user=project.creator, save=True)
    content_type_for_query = ContentType.objects.get_for_model(project)
    # root folder + file + deleted_file = 3 BaseFileNodes
    assert BaseFileNode.objects.filter(target_object_id=project.id, target_content_type=content_type_for_query).count() == 3
    # root folder + file = 2 BaseFileNodes
    assert BaseFileNode.active.filter(target_object_id=project.id, target_content_type=content_type_for_query).count() == 2

def test_folder_update_calls_folder_update_method(project, create_test_file):
    file = create_test_file(target=project)
    parent_folder = file.parent
    # the folder update method should be the Folder.update method
    assert parent_folder.__class__.update == Folder.update
    # the folder update method should not be the File update method
    assert parent_folder.__class__.update != File.update
    # the file update method should be the File update method
    assert file.__class__.update == File.update


def test_file_update_respects_region(project, user, create_test_file):
    test_file = create_test_file(target=project)
    version = test_file.versions.first()
    original_region = project.osfstorage_region
    assert version.region == original_region

    # update the region on the project, ensure the new version has the new region
    node_settings = NodeSettings.objects.get(owner=project.id)
    new_region = RegionFactory()
    node_settings.region = new_region
    node_settings.save()
    test_file.save()

    new_version = test_file.create_version(
        user, {
            'service': 'cloud',
            osfstorage_settings.WATERBUTLER_RESOURCE: 'osf',
            'object': '07d80a',
        }, {
            'sha256': 'existing',
        }
    )
    assert new_region != original_region
    assert new_version.region == new_region
