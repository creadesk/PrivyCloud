'''
parse_duration() – alle unterstützten Einheiten, Sonderfälle und ungültige Eingaben.
DurationField – korrekte Umwandlung, Choice-Validierung, Pflichtfeldprüfung und ungültige Werte.
'''

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django import forms

from paas.fields import parse_duration, DurationField


class ParseDurationTests(SimpleTestCase):
    """Tests für die Funktion parse_duration()."""

    def test_no_limit_returns_none(self):
        self.assertIsNone(parse_duration('no_limit'))

    def test_hours(self):
        self.assertEqual(parse_duration('1h'), timedelta(hours=1))
        self.assertEqual(parse_duration('24h'), timedelta(hours=24))
        self.assertEqual(parse_duration('0h'), timedelta(hours=0))

    def test_days(self):
        self.assertEqual(parse_duration('1d'), timedelta(days=1))
        self.assertEqual(parse_duration('2d'), timedelta(days=2))
        self.assertEqual(parse_duration('30d'), timedelta(days=30))

    def test_weeks(self):
        self.assertEqual(parse_duration('1w'), timedelta(weeks=1))
        self.assertEqual(parse_duration('2w'), timedelta(weeks=2))

    def test_months(self):
        # Ein Monat wird mit 30 Tagen berechnet.
        self.assertEqual(parse_duration('1m'), timedelta(days=30))
        self.assertEqual(parse_duration('2m'), timedelta(days=60))
        self.assertEqual(parse_duration('12m'), timedelta(days=360))

    def test_large_duration(self):
        self.assertEqual(
            parse_duration('1000d'),
            timedelta(days=1000)
        )

    def test_invalid_values_raise_validation_error(self):
        invalid_values = [
            '',
            ' ',
            '1',
            'h',
            '1x',
            '1H',
            '1D',
            '1W',
            '1M',
            '-1h',
            '+1h',
            '1.5h',
            '1 h',
            'abc',
            'no limit',
            'No_Limit',
            '1hh',
            '1h2d',
        ]

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    parse_duration(value)

    def test_none_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            parse_duration(None)

    def test_error_message_contains_invalid_value(self):
        with self.assertRaises(ValidationError) as context:
            parse_duration('invalid')

        self.assertIn(
            'invalid',
            str(context.exception)
        )


class DurationFieldTests(SimpleTestCase):
    """Tests für das benutzerdefinierte DurationField."""

    def setUp(self):
        self.choices = [
            ('no_limit', 'Ohne Limit'),
            ('1h', '1 Stunde'),
            ('2h', '2 Stunden'),
            ('1d', '1 Tag'),
            ('1w', '1 Woche'),
            ('1m', '1 Monat'),
        ]

        self.field = DurationField(
            choices=self.choices,
        )

    def test_valid_hour_returns_timedelta(self):
        result = self.field.clean('1h')

        self.assertEqual(result, timedelta(hours=1))

    def test_valid_day_returns_timedelta(self):
        result = self.field.clean('1d')

        self.assertEqual(result, timedelta(days=1))

    def test_valid_week_returns_timedelta(self):
        result = self.field.clean('1w')

        self.assertEqual(result, timedelta(weeks=1))

    def test_valid_month_returns_timedelta(self):
        result = self.field.clean('1m')

        self.assertEqual(result, timedelta(days=30))

    def test_no_limit_returns_none(self):
        result = self.field.clean('no_limit')

        self.assertIsNone(result)

    def test_clean_returns_timedelta_not_string(self):
        result = self.field.clean('2h')

        self.assertIsInstance(result, timedelta)
        self.assertNotIsInstance(result, str)

    def test_invalid_choice_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            self.field.clean('5h')

    def test_empty_value_raises_validation_error(self):
        for value in ('', None):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    self.field.clean(value)

    def test_required_field(self):
        field = DurationField(
            choices=self.choices,
            required=True,
        )

        with self.assertRaises(ValidationError):
            field.clean('')

    def test_optional_field_empty_value(self):
        field = DurationField(
            choices=self.choices,
            required=False,
        )

        self.assertIsNone(field.clean(''))
        self.assertIsNone(field.clean(None))

    def test_widget_is_select(self):
        self.assertIsInstance(
            self.field.widget,
            forms.Select
        )

    def test_original_choice_string_is_preserved_for_validation(self):
        # to_python() soll den ursprünglichen String behalten.
        self.assertEqual(
            self.field.to_python('2h'),
            '2h'
        )

    def test_to_python_rejects_empty_value(self):
        for value in ('', None):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    self.field.to_python(value)

    def test_to_python_does_not_convert_to_timedelta(self):
        result = self.field.to_python('1d')

        self.assertEqual(result, '1d')
        self.assertIsInstance(result, str)


class DurationFieldIntegrationTests(SimpleTestCase):
    """Tests für DurationField innerhalb eines Django-Formulars."""

    def setUp(self):
        class TestForm(forms.Form):
            duration = DurationField(
                choices=[
                    ('no_limit', 'Ohne Limit'),
                    ('1h', '1 Stunde'),
                    ('1d', '1 Tag'),
                    ('1w', '1 Woche'),
                ]
            )

        self.form_class = TestForm

    def test_valid_form(self):
        form = self.form_class({
            'duration': '2h',
        })

        # 2h ist hier keine definierte Choice und deshalb ungültig.
        self.assertFalse(form.is_valid())

    def test_valid_form_with_defined_choice(self):
        form = self.form_class({
            'duration': '1h',
        })

        self.assertTrue(form.is_valid())
        self.assertEqual(
            form.cleaned_data['duration'],
            timedelta(hours=1)
        )

    def test_no_limit_in_form(self):
        form = self.form_class({
            'duration': 'no_limit',
        })

        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data['duration'])

    def test_invalid_choice_in_form(self):
        form = self.form_class({
            'duration': '99d',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('duration', form.errors)

    def test_missing_duration_in_form(self):
        form = self.form_class({})

        self.assertFalse(form.is_valid())
        self.assertIn('duration', form.errors)
