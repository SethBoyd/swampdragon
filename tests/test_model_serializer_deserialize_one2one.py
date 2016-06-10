from swampdragon.serializers.model_serializer import ModelSerializer
from swampdragon.testing.dragon_testcase import DragonTestCase
from .models import SDModel
from django.db import models


class FooOne2One(SDModel):
    name = models.CharField(max_length=20)


class BarOne2One(SDModel):
    foo = models.OneToOneField(FooOne2One)
    number = models.IntegerField()


class FooSerializer(ModelSerializer):
    bar = 'BarSerializer'

    class Meta:
        model = FooOne2One
        update_fields = ('name', 'bar', 'id')


class BarSerializer(ModelSerializer):
    foo = FooSerializer

    class Meta:
        model = BarOne2One
        update_fields = ('number', 'foo', 'id')


class TestModelSerializer(DragonTestCase):
    def test_deserialize_with_one_2_one(self):
        data = {
            'name': 'foo',
            'bar': {'number': 5}
        }
        serializer = FooSerializer(data)
        foo = serializer.save()
        self.assertEqual(foo.name, data['name'])
        self.assertEqual(foo.bar.number, data['bar']['number'])

    def test_deserialize_with_reverse_one_2_one(self):
        data = {
            'number': 123,
            'foo': {'name': 'foo'}
        }
        serializer = BarSerializer(data)
        bar = serializer.save()
        self.assertEqual(bar.number, data['number'])
        self.assertEqual(bar.foo.name, data['foo']['name'])

    def test_deserialize_update_with_one_2_one(self):
        foo = FooOne2One(name="foo")
        foo.save()
        bar = BarOne2One(number=123, foo=foo)
        bar.save()
        data = {
            'id': foo.id,
            'name': 'updatefoo',
            'bar': {
                'id': bar.id,
                'number': 321,
            }
        }
        serializer = FooSerializer(data=data, instance=bar)
        foo_serialized = serializer.save()

        #ensure these changes were saved to correct instance
        bar_check = BarOne2One.objects.get(pk=bar.id)
        self.assertEqual(foo_serialized.name, data['name'])
        self.assertEqual(foo_serialized.bar.id, data['bar']['id'])
        self.assertEqual(foo_serialized.bar.number, data['bar']['number'])
        self.assertEqual(bar_check.id, data['bar']['id'])
        self.assertEqual(bar_check.number, data['bar']['number'])

    def test_deserialize_update_with_reverse_one_2_one(self):
        foo = FooOne2One(name="foo")
        foo.save()
        bar = BarOne2One(number=123, foo=foo)
        bar.save()
        data = {
            'id': bar.id,
            'number': 321,
            'foo': {
                'id': foo.id,
                'name': 'updatefoo'
            }
        }
        serializer = BarSerializer(data=data, instance=bar)
        bar_serialized = serializer.save()

        #ensure these changes were saved to correct instance
        foo_check = FooOne2One.objects.get(pk=foo.id)
        self.assertEqual(bar_serialized.number, data['number'])
        self.assertEqual(bar_serialized.foo.id, data['foo']['id'])
        self.assertEqual(bar_serialized.foo.name, data['foo']['name'])
        self.assertEqual(foo_check.id, data['foo']['id'])
        self.assertEqual(foo_check.name, data['foo']['name'])
