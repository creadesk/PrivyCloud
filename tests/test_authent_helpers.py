
import string

from django.test import SimpleTestCase

from authent.views import (
    username_is_banned,
    generateRandomString,
)


class UsernameIsBannedTests(SimpleTestCase):

    def test_exact_banned_username(self):
        self.assertTrue(username_is_banned('delete_me'))

    def test_exact_banned_username_case_insensitive(self):
        self.assertTrue(username_is_banned('DELETE_ME'))
        self.assertTrue(username_is_banned('Delete_Me'))

    def test_banned_prefix(self):
        self.assertTrue(username_is_banned('delete_me_user'))
        self.assertTrue(username_is_banned('delete_me_123'))

    def test_banned_prefix_case_insensitive(self):
        self.assertTrue(username_is_banned('DELETE_ME_USER'))
        self.assertTrue(username_is_banned('Delete_Me_User'))

    def test_allowed_username(self):
        self.assertFalse(username_is_banned('normaluser'))
        self.assertFalse(username_is_banned('test_delete_me'))
        self.assertFalse(username_is_banned('delete'))
        self.assertFalse(username_is_banned('delete_meuser'))

    def test_empty_username(self):
        self.assertFalse(username_is_banned(''))


class GenerateRandomStringTests(SimpleTestCase):

    def test_default_length(self):
        result = generateRandomString()

        self.assertEqual(len(result), 20)

    def test_custom_length(self):
        result = generateRandomString(50)

        self.assertEqual(len(result), 50)

    def test_zero_length(self):
        result = generateRandomString(0)

        self.assertEqual(result, '')

    def test_generated_string_contains_only_allowed_characters(self):
        result = generateRandomString(100)

        allowed_characters = string.ascii_letters + string.digits

        self.assertTrue(
            all(char in allowed_characters for char in result)
        )

    def test_generated_string_has_requested_length(self):
        for length in (1, 5, 10, 20, 100):
            with self.subTest(length=length):
                self.assertEqual(
                    len(generateRandomString(length)),
                    length
                )
