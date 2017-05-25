from django.core.exceptions import ValidationError
try:
    # bis 1.8.x
    from django.db.models.fields.related import ReverseSingleRelatedObjectDescriptor
    from django.db.models.fields.related import ManyRelatedObjectsDescriptor
    from django.db.models.fields.related import ReverseManyRelatedObjectsDescriptor
    from django.db.models.fields.related import ForeignRelatedObjectsDescriptor
    from django.db.models.fields.related import SingleRelatedObjectDescriptor
    pre19syntax = True
except:
    # ab 1.9.0
    from django.db.models.fields.related import ForwardManyToOneDescriptor
    from django.db.models.fields.related import ManyToManyDescriptor
    from django.db.models.fields.related import ReverseManyToOneDescriptor
    from django.db.models.fields.related import ReverseOneToOneDescriptor
    pre19syntax = False

from swampdragon.model_tools import get_property, get_model
from swampdragon.serializers.field_serializers import serialize_field
from swampdragon.serializers.object_map import get_object_map
from swampdragon.serializers.serializer import Serializer
from swampdragon.serializers.serializer_importer import get_serializer
from swampdragon.serializers.field_deserializers import get_deserializer
from swampdragon.serializers.serializer_tools import get_id_mappings
from swampdragon.serializers.validation import ModelValidationError


class ModelSerializerMeta(object):
    def __init__(self, options):
        self.model = get_model(getattr(options, 'model'))
        self.publish_fields = getattr(options, 'publish_fields', None)

        if not self.publish_fields:
            self.publish_fields = self.get_fields(self.model)

        if isinstance(self.publish_fields, str):
            self.publish_fields = (self.publish_fields, )

        self.update_fields = getattr(options, 'update_fields', ())
        if isinstance(self.update_fields, str):
            self.update_fields = (self.update_fields, )

        self.id_field = getattr(options, 'id_field', 'pk')
        self.base_channel = getattr(options, 'base_channel', self.model._meta.model_name)

    def get_fields(self, model):
        fields = []
        for f in model._meta.get_all_field_names():
            field = model._meta.get_field_by_name(f)[0]
            if hasattr(field, 'get_accessor_name'):
                fields.append(field.get_accessor_name())
            else:
                fields.append(field.name)
        return fields


class ModelSerializer(Serializer):
    def __init__(self, data=None, instance=None, initial=None):
        if data and not isinstance(data, dict):
            raise Exception('data needs to be a dictionary')
        self.opts = ModelSerializerMeta(self.Meta)
        self._instance = instance
        self._data = data
        self.initial = initial or {}
        self.base_fields = self._get_base_fields()
        self.m2m_fields = self._get_m2m_fields()
        self.related_fields = self._get_related_fields()
        self.errors = {}

        try:
            if self.instance == None and self.opts.model._meta.pk.name in self._data:
                self._instance = self.opts.model.objects.get(pk=self._data[self.opts.model._meta.pk.name])
        except Exception as e:
            pass

    class Meta(object):
        pass

    @property
    def instance(self):
        return self._instance

    def _get_base_fields(self):
        return [f.name for f in self.opts.model._meta.fields]

    def _get_related_fields(self):
        return [f for f in self.opts.update_fields if f not in self.base_fields and f not in self.m2m_fields]

    def _get_m2m_fields(self):
        related_m2m = [f.get_accessor_name() for f in self.opts.model._meta.get_all_related_many_to_many_objects()]
        m2m_fields = [f.name for f in self.opts.model._meta.local_many_to_many]
        m2m = m2m_fields + related_m2m
        return [f for f in self.opts.update_fields if f in m2m]

    def deserialize(self):
        # Set initial data
        if not self._instance:
            self._instance = self.opts.model()

        for key, val in self.initial.items():
            setattr(self.instance, key, val)

        # Deserialize base fields
        for key, val in self._data.items():
            if key not in self.opts.update_fields or (key not in self.base_fields and not key.endswith('_id')):
                continue
            try:
                self.validate_field(key, val, self._data)
                self._deserialize_field(key, val)
            except ModelValidationError as err:
                self.errors.update(err.get_error_dict())

        if self.errors:
            raise ModelValidationError(errors=self.errors)

        return self.instance

    def save(self):
        self.deserialize()
        if self.errors:
            raise ModelValidationError(self.errors)
        try:
            self.instance.clean_fields()
        except ValidationError as e:
            raise ModelValidationError(e.message_dict)
        self.instance.save()

        # Serialize related fields
        for key, val in self._data.items():
            if key not in self.related_fields:
                continue
            self._deserialize_related(key, val, save_instance=True)

        # Serialize m2m fields
        for key, val in self._data.items():
            if key not in self.m2m_fields:
                continue
            self._deserialize_related(key, val, save_instance=True)
        return self.instance

    def needs_saved(self):
        return bool(getattr(self.instance, self.opts.model._meta.pk.name, False))

    def _deserialize_field(self, key, val):
        if hasattr(self, key):
            serializer = self._get_related_serializer(key)
            if val != None:
                value = serializer(val).save()
                setattr(self.instance, key, value)
                value.save()
            else:
                setattr(self.instance, key, val)
            return

        field = self.opts.model._meta.get_field(key)
        field_type = field.__class__.__name__
        deserializer = get_deserializer(field_type)
        if deserializer:
            deserializer(self.instance, key, val)
        else:
            setattr(self.instance, key, val)

    def _deserialize_related(self, key, val, save_instance=False):
        serializer = self._get_related_serializer(key)
        if isinstance(val, list):
            for v in val:
                serializer_instance = serializer(data=val)
                if save_instance:
                    related_instance = serializer_instance.save()
                else:
                    related_instance = serializer_instance.deserialize()
                getattr(self.instance, key).add(related_instance)
        else:
            if serializer:
                serializer_instance = serializer(data=val)
                if save_instance and serializer_instance.needs_saved():
                    related_instance = serializer_instance.save()
                else:
                    related_instance = serializer_instance.deserialize()
                setattr(self.instance, key, related_instance)
            else:
                setattr(self.instance, key, val)

    def _get_related_serializer(self, key):
        serializer = getattr(self, key, None)
        if isinstance(serializer, str):
            return get_serializer(serializer, self.__class__)
        return serializer

    def get_object_map_data(self):
        return {
            'id': getattr(self.instance, self.opts.id_field),
            '_type': self.opts.model._meta.model_name
        }

    def serialize(self, fields=None, ignore_serializers=None):
        if not fields:
            fields = self.opts.publish_fields
        if not self.instance:
            return None

        data = self.get_object_map_data()

        # Set all the ids for related models
        # so the datamapper can find the connection
        id_mappings = get_id_mappings(self)
        data.update(id_mappings)
        if id_mappings:
            for related_model in id_mappings.iterkeys():
                if related_model in self.opts.publish_fields and related_model not in fields:
                    # use serializer for related model if in publish_fields
                    fields.append(related_model)


        # Serialize the fields
        for field in fields:
            data[field] = self._serialize_value(field, ignore_serializers)

        custom_serializer_functions = self._get_custom_field_serializers()
        for custom_function, name in custom_serializer_functions:
            serializer = getattr(self, name, None)
            if serializer:
                serializer = get_serializer(serializer, self)
                data[name] = custom_function(self.instance, serializer)
            else:
                data[name] = custom_function(self.instance)

        return data

    def _serialize_value(self, attr_name, ignore_serializers=None):
        obj_serializer = self._get_related_serializer(attr_name)
        # To prevent infinite recursion, allow serializers to be ignored
        if ignore_serializers and obj_serializer in ignore_serializers:
            return None

        val = get_property(self.instance, attr_name)

        # If we have one or more related models
        if obj_serializer and hasattr(val, 'all'):
            return [obj_serializer(instance=o).serialize(ignore_serializers=[self.__class__]) for o in val.all()]
        elif obj_serializer:
            return obj_serializer(instance=val).serialize(ignore_serializers=[self.__class__])
        elif hasattr(self.opts.model, attr_name):
            # Check if the field is a relation of any kind
            field_type = getattr(self.opts.model, attr_name)
            if pre19syntax:
                # Reverse FK
                if isinstance(field_type, ReverseSingleRelatedObjectDescriptor):
                    rel = get_property(self.instance, attr_name)
                    if rel:
                        val = rel.pk
                # FK
                elif isinstance(field_type, ForeignRelatedObjectsDescriptor):
                    val = list(get_property(self.instance, attr_name).all().values_list('pk', flat=True))
                elif isinstance(field_type, ReverseManyRelatedObjectsDescriptor):
                    val = list(get_property(self.instance, attr_name).all().values_list('pk', flat=True))
                elif isinstance(field_type, ManyRelatedObjectsDescriptor):
                    val = list(get_property(self.instance, attr_name).all().values_list('pk', flat=True))
            else:
                if isinstance(field_type, ForwardManyToOneDescriptor):
                    rel = get_property(self.instance, attr_name)
                    if rel:
                        val = rel.pk
                elif isinstance(field_type, ReverseManyToOneDescriptor):
                    val = list(get_property(self.instance, attr_name).all().values_list('pk', flat=True))
                elif isinstance(field_type, ManyToManyDescriptor) and field_type.reverse:
                    al = list(get_property(self.instance, attr_name).all().values_list('pk', flat=True))
                elif isinstance(field_type, ManyToManyDescriptor) and not field_type.reverse:
                    val = list(get_property(self.instance, attr_name).all().values_list('pk', flat=True))
        # Serialize the field
        return serialize_field(val)

    @classmethod
    def get_object_map(cls, include_serializers=None, ignore_serializers=None):
        return get_object_map(cls, ignore_serializers)

    @classmethod
    def get_base_channel(cls):
        if hasattr(cls.Meta, 'base_channel'):
            return '{}|'.format(getattr(cls.Meta, 'base_channel'))
        return '{}|'.format(get_model(cls.Meta.model)._meta.model_name)

    @classmethod
    def get_related_serializers(cls):
        possible_serializers = [k for k in cls.__dict__.keys() if not k.startswith('_') and not k == 'Meta']
        serializers = []
        for possible_serializer in possible_serializers:
            val = getattr(cls, possible_serializer)
            if isinstance(val, str):
                val = get_serializer(val, cls)
            if hasattr(val, 'serialize'):
                serializers.append((val, possible_serializer))
        return serializers
