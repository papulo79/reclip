import unittest

from helpers import AppHarness, basic_auth


class AuthDisabledTest(unittest.TestCase):
    def test_open_when_not_configured(self):
        harness = AppHarness()
        harness.start()
        try:
            client = harness.client
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.get("/api/jobs").status_code, 200)
            self.assertEqual(client.get("/api/files").status_code, 200)
        finally:
            harness.stop()


class AuthEnabledTest(unittest.TestCase):
    def setUp(self):
        self.harness = AppHarness(
            env={"RECLIP_USER": "ana", "RECLIP_PASSWORD": "secreto"}
        )
        self.harness.start()
        self.client = self.harness.client

    def tearDown(self):
        self.harness.stop()

    def test_index_requires_credentials(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 401)
        self.assertIn("Basic", response.headers["WWW-Authenticate"])

    def test_api_requires_credentials(self):
        for path in ("/api/jobs", "/api/files", "/api/status/x"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        self.assertEqual(
            self.client.post("/api/download", json={"url": "https://example.com/a"}).status_code,
            401,
        )

    def test_static_requires_credentials(self):
        self.assertEqual(self.client.get("/static/app.js").status_code, 401)

    def test_wrong_password_is_rejected(self):
        response = self.client.get("/", headers=basic_auth("ana", "otra"))
        self.assertEqual(response.status_code, 401)

    def test_wrong_user_is_rejected(self):
        response = self.client.get("/", headers=basic_auth("pepe", "secreto"))
        self.assertEqual(response.status_code, 401)

    def test_valid_credentials_are_accepted(self):
        headers = basic_auth("ana", "secreto")
        self.assertEqual(self.client.get("/", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/jobs", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/static/app.js", headers=headers).status_code, 200)

    def test_partial_configuration_disables_auth(self):
        self.harness.stop()
        self.harness = AppHarness(env={"RECLIP_USER": "ana", "RECLIP_PASSWORD": ""})
        self.harness.start()
        self.client = self.harness.client
        self.assertEqual(self.client.get("/").status_code, 200)


if __name__ == "__main__":
    unittest.main()
