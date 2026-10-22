import os
import tempfile
import unittest

import context  # noqa: F401
from vmboxd.auth import Auth, AuthError, hash_password, verify_password


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auth = Auth(os.path.join(self.tmp.name, "auth.json"), iterations=1000)

    def tearDown(self):
        self.tmp.cleanup()

    def test_hash(self):
        encoded = hash_password("secret", iterations=1000)
        self.assertTrue(verify_password("secret", encoded))
        self.assertFalse(verify_password("Secret", encoded))
        self.assertFalse(verify_password("secret", "garbage"))

    def test_setup_login_lock(self):
        self.assertTrue(self.auth.setup_required)
        token = self.auth.setup("geheim123")
        self.assertTrue(self.auth.valid(token))
        self.assertEqual(os.stat(self.auth.path).st_mode & 0o777, 0o600)
        self.auth.lock()
        self.assertFalse(self.auth.valid(token))
        token = self.auth.login("geheim123")
        self.assertTrue(self.auth.valid(token))
        self.assertFalse(self.auth.locked)

    def test_bruteforce_backoff(self):
        self.auth.setup("geheim123")
        for _ in range(3):
            with self.assertRaises(AuthError):
                self.auth.login("falsch")
        with self.assertRaises(AuthError) as ctx:
            self.auth.login("geheim123")
        self.assertIn("warten", str(ctx.exception))

    def test_change_password(self):
        self.auth.setup("geheim123")
        with self.assertRaises(AuthError):
            self.auth.change_password("falsch", "neues-passwort")
        self.auth.change_password("geheim123", "neues-passwort")
        self.auth.login("neues-passwort")


if __name__ == "__main__":
    unittest.main()
