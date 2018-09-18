from rest_framework import serializers as ser
from api.base.utils import absolute_reverse
from api.base.serializers import JSONAPISerializer, RelationshipField, IDField, LinksField


class RegistrationIdentifierSerializer(JSONAPISerializer):

    category = ser.SerializerMethodField()

    filterable_fields = frozenset(['category'])

    value = ser.CharField(read_only=True)

    referent = RelationshipField(
        related_view='registrations:registration-detail',
        related_view_kwargs={'node_id': '<referent._id>'},
    )

    id = IDField(source='_id', read_only=True)

    links = LinksField({'self': 'self_url'})

    class Meta:
        type_ = 'identifiers'

    def get_category(self, obj):
        if obj.category == 'legacy_doi':
            return 'doi'
        return obj.category

    def get_absolute_url(self, obj):
        return obj.absolute_api_v2_url

    def get_id(self, obj):
        return obj._id

    def get_detail_url(self, obj):
        return '{}/identifiers/{}'.format(obj.absolute_api_v2_url, obj._id)

    def self_url(self, obj):
        return absolute_reverse(
            'identifiers:identifier-detail', kwargs={
                'identifier_id': obj._id,
                'version': self.context['request'].parser_context['kwargs']['version'],
            },
        )


class NodeIdentifierSerializer(RegistrationIdentifierSerializer):

    referent = RelationshipField(
        related_view='nodes:node-detail',
        related_view_kwargs={'node_id': '<referent._id>'},
    )


class PreprintIdentifierSerializer(RegistrationIdentifierSerializer):

    referent = RelationshipField(
        related_view='preprints:preprint-detail',
        related_view_kwargs={'preprint_id': '<referent._id>'},
    )
