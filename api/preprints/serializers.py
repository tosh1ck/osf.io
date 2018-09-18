from django.core.exceptions import ValidationError
from rest_framework import exceptions
from rest_framework import serializers as ser

from api.base.exceptions import Conflict
from api.base.serializers import (
    JSONAPISerializer, IDField, TypeField, HideIfNotWithdrawal, NoneIfWithdrawal,
    LinksField, RelationshipField, VersionedDateTimeField, JSONAPIListField,
)
from api.base.utils import absolute_reverse, get_user_auth
from api.nodes.serializers import (
    NodeCitationSerializer,
    NodeLicenseSerializer,
    get_license_details,
    NodeTagField,
)
from api.taxonomies.serializers import TaxonomizableSerializerMixin
from framework.exceptions import PermissionsError
from website.exceptions import NodeStateError
from website.project import signals as project_signals
from osf.models import BaseFileNode, PreprintService, PreprintProvider, Node, NodeLicense
from osf.utils import permissions


class PrimaryFileRelationshipField(RelationshipField):
    def get_object(self, file_id):
        return BaseFileNode.load(file_id)

    def to_internal_value(self, data):
        file = self.get_object(data)
        return {'primary_file': file}

class NodeRelationshipField(RelationshipField):
    def get_object(self, node_id):
        return Node.load(node_id)

    def to_internal_value(self, data):
        node = self.get_object(data)
        return {'node': node}

class PreprintProviderRelationshipField(RelationshipField):
    def get_object(self, node_id):
        return PreprintProvider.load(node_id)

    def to_internal_value(self, data):
        provider = self.get_object(data)
        return {'provider': provider}


class PreprintLicenseRelationshipField(RelationshipField):
    def to_internal_value(self, license_id):
        license = NodeLicense.load(license_id)
        if license:
            return {'license_type': license}
        raise exceptions.NotFound('Unable to find specified license.')


class PreprintSerializer(TaxonomizableSerializerMixin, JSONAPISerializer):
    filterable_fields = frozenset([
        'id',
        'date_created',
        'date_modified',
        'date_published',
        'original_publication_date',
        'provider',
        'is_published',
        'subjects',
        'reviews_state',
        'node_is_public',
    ])

    id = IDField(source='_id', read_only=True)
    type = TypeField()

    date_created = VersionedDateTimeField(source='created', read_only=True)
    date_modified = VersionedDateTimeField(source='modified', read_only=True)
    date_published = VersionedDateTimeField(read_only=True)
    original_publication_date = VersionedDateTimeField(required=False)
    doi = ser.CharField(source='article_doi', required=False, allow_null=True)
    title = ser.CharField(source='node.title', required=False)
    description = ser.CharField(required=False, allow_blank=True, allow_null=True, source='node.description')
    date_withdrawn = VersionedDateTimeField(read_only=True, allow_null=True)
    withdrawal_justification = HideIfNotWithdrawal(ser.CharField(required=False, read_only=True, allow_blank=True))
    is_published = NoneIfWithdrawal(ser.BooleanField(required=False))
    is_preprint_orphan = NoneIfWithdrawal(ser.BooleanField(read_only=True))
    license_record = NodeLicenseSerializer(required=False, source='license')
    tags = JSONAPIListField(child=NodeTagField(), required=False, source='node.tags')
    node_is_public = NoneIfWithdrawal(ser.BooleanField(read_only=True, source='node__is_public'))
    preprint_doi_created = NoneIfWithdrawal(VersionedDateTimeField(read_only=True))

    contributors = RelationshipField(
        related_view='nodes:node-contributors',
        related_view_kwargs={'node_id': '<node._id>'},
    )
    reviews_state = ser.CharField(source='machine_state', read_only=True, max_length=15)
    date_last_transitioned = NoneIfWithdrawal(VersionedDateTimeField(read_only=True))

    citation = NoneIfWithdrawal(RelationshipField(
        related_view='preprints:preprint-citation',
        related_view_kwargs={'preprint_id': '<_id>'},
    ))

    identifiers = NoneIfWithdrawal(RelationshipField(
        related_view='preprints:identifier-list',
        related_view_kwargs={'preprint_id': '<_id>'},
    ))

    node = NoneIfWithdrawal(NodeRelationshipField(
        related_view='nodes:node-detail',
        related_view_kwargs={'node_id': '<node._id>'},
        read_only=False,
    ))

    license = PreprintLicenseRelationshipField(
        related_view='licenses:license-detail',
        related_view_kwargs={'license_id': '<license.node_license._id>'},
        read_only=False,
    )

    provider = PreprintProviderRelationshipField(
        related_view='providers:preprint-providers:preprint-provider-detail',
        related_view_kwargs={'provider_id': '<provider._id>'},
        read_only=False,
    )

    files = NoneIfWithdrawal(RelationshipField(
        related_view='nodes:node-storage-providers',
        related_view_kwargs={'node_id': '<_id>'},
    ))

    primary_file = NoneIfWithdrawal(PrimaryFileRelationshipField(
        related_view='files:file-detail',
        related_view_kwargs={'file_id': '<primary_file._id>'},
        read_only=False,
    ))

    review_actions = RelationshipField(
        related_view='preprints:preprint-review-action-list',
        related_view_kwargs={'preprint_id': '<_id>'},
    )

    requests = NoneIfWithdrawal(RelationshipField(
        related_view='preprints:preprint-request-list',
        related_view_kwargs={'preprint_id': '<_id>'},
    ))

    links = LinksField(
        {
            'self': 'get_preprint_url',
            'html': 'get_absolute_html_url',
            'doi': 'get_article_doi_url',
            'preprint_doi': 'get_preprint_doi_url',
        },
    )

    class Meta:
        type_ = 'preprints'

    def get_preprint_url(self, obj):
        return absolute_reverse('preprints:preprint-detail', kwargs={'preprint_id': obj._id, 'version': self.context['request'].parser_context['kwargs']['version']})

    def get_absolute_url(self, obj):
        return self.get_preprint_url(obj)

    def get_article_doi_url(self, obj):
        return 'https://doi.org/{}'.format(obj.article_doi) if obj.article_doi else None

    def get_preprint_doi_url(self, obj):
        doi = None
        doi_identifier = obj.get_identifier('doi')
        if doi_identifier:
            doi = doi_identifier.value
        # if a preprint hasn't been published yet, don't show the DOI prematurely
        elif obj.is_published:
            client = obj.get_doi_client()
            doi = client.build_doi(preprint=obj) if client else None
        return 'https://doi.org/{}'.format(doi) if doi else None

    def update(self, preprint, validated_data):
        assert isinstance(preprint, PreprintService), 'You must specify a valid preprint to be updated'
        assert isinstance(preprint.node, Node), 'You must specify a preprint with a valid node to be updated.'

        auth = get_user_auth(self.context['request'])
        if not preprint.node.has_permission(auth.user, 'admin'):
            raise exceptions.PermissionDenied(detail='User must be an admin to update a preprint.')

        published = validated_data.pop('is_published', None)
        if published and preprint.provider.is_reviewed:
            raise Conflict('{} uses a moderation workflow, so preprints must be submitted for review instead of published directly. Submit a preprint by creating a `submit` Action at {}'.format(
                preprint.provider.name,
                absolute_reverse(
                    'preprints:preprint-review-action-list', kwargs={
                        'version': self.context['request'].parser_context['kwargs']['version'],
                        'preprint_id': preprint._id,
                    },
                ),
            ))

        save_node = False
        save_preprint = False
        recently_published = False
        primary_file = validated_data.pop('primary_file', None)
        if primary_file:
            self.set_field(preprint.set_primary_file, primary_file, auth)
            save_node = True

        old_tags = set(preprint.node.tags.values_list('name', flat=True))
        if validated_data.get('node') and 'tags' in validated_data['node']:
            current_tags = set(validated_data['node'].pop('tags', []))
        elif self.partial:
            current_tags = set(old_tags)
        else:
            current_tags = set()

        for new_tag in (current_tags - old_tags):
            preprint.node.add_tag(new_tag, auth=auth)
        for deleted_tag in (old_tags - current_tags):
            preprint.node.remove_tag(deleted_tag, auth=auth)

        if 'node' in validated_data:
            preprint.node.update(fields=validated_data.pop('node'))
            save_node = True

        if 'subjects' in validated_data:
            subjects = validated_data.pop('subjects', None)
            self.set_field(preprint.set_subjects, subjects, auth)
            save_preprint = True

        if 'article_doi' in validated_data:
            preprint.node.preprint_article_doi = validated_data['article_doi']
            save_node = True

        if 'license_type' in validated_data or 'license' in validated_data:
            license_details = get_license_details(preprint, validated_data)
            self.set_field(preprint.set_preprint_license, license_details, auth)
            save_preprint = True

        if 'original_publication_date' in validated_data:
            preprint.original_publication_date = validated_data['original_publication_date']
            save_preprint = True

        if published is not None:
            if not preprint.primary_file:
                raise exceptions.ValidationError(detail='A valid primary_file must be set before publishing a preprint.')
            self.set_field(preprint.set_published, published, auth)
            save_preprint = True
            recently_published = published
            preprint.node.set_privacy('public')
            save_node = True

        if save_node:
            try:
                preprint.node.save()
            except ValidationError as e:
                # Raised from invalid DOI
                raise exceptions.ValidationError(detail=e.messages[0])

        if save_preprint:
            preprint.save()

        # Send preprint confirmation email signal to new authors on preprint! -- only when published
        # TODO: Some more thought might be required on this; preprints made from existing
        # nodes will send emails making it seem like a new node.
        if recently_published:
            for author in preprint.node.contributors:
                if author != auth.user:
                    project_signals.contributor_added.send(preprint.node, contributor=author, auth=auth, email_template='preprint')

        return preprint

    def set_field(self, func, val, auth, save=False):
        try:
            func(val, auth)
        except PermissionsError as e:
            raise exceptions.PermissionDenied(detail=e.message)
        except (ValueError, ValidationError, NodeStateError) as e:
            raise exceptions.ValidationError(detail=e.message)


class PreprintCreateSerializer(PreprintSerializer):
    # Overrides PreprintSerializer to make id nullable, adds `create`
    id = IDField(source='_id', required=False, allow_null=True)

    def create(self, validated_data):
        node = validated_data.pop('node', {})
        if isinstance(node, dict):
            node = Node.objects.create(creator=self.context['request'].user, **node)

        if node.is_deleted:
            raise exceptions.ValidationError('Cannot create a preprint from a deleted node.')

        auth = get_user_auth(self.context['request'])
        if not node.has_permission(auth.user, permissions.ADMIN):
            raise exceptions.PermissionDenied

        provider = validated_data.pop('provider', None)
        if not provider:
            raise exceptions.ValidationError(detail='You must specify a valid provider to create a preprint.')

        node_preprints = node.preprints.filter(provider=provider)
        if node_preprints.exists():
            raise Conflict('Only one preprint per provider can be submitted for a node. Check `meta[existing_resource_id]`.', meta={'existing_resource_id': node_preprints.first()._id})

        preprint = PreprintService(node=node, provider=provider)
        preprint.save()
        preprint.node._has_abandoned_preprint = True
        preprint.node.save()

        return self.update(preprint, validated_data)


class PreprintCitationSerializer(NodeCitationSerializer):

    class Meta:
        type_ = 'preprint-citation'
