import json
import unittest

from helpers import (
    AppHarness,
    job_status,
    post_download,
    wait_for_status,
    wait_until_idle,
)


class ReorderQueueTest(unittest.TestCase):
    def setUp(self):
        self.harness = AppHarness(delay="0.5")
        self.harness.start()
        self.client = self.harness.client

    def tearDown(self):
        self.harness.stop()

    def test_move_queued_jobs_up_and_down(self):
        client = self.client
        first = post_download(client, "https://example.com/a", "A")
        second = post_download(client, "https://example.com/b", "B")
        third = post_download(client, "https://example.com/c", "C")

        self.assertEqual(first["status"], "downloading")
        self.assertEqual(second["position"], 1)
        self.assertEqual(third["position"], 2)

        # C sube al primer puesto
        response = client.post(
            f"/api/queue/{third['job_id']}/move", json={"direction": "up"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["position"], 1)
        self.assertEqual(response.get_json()["queue"], [third["job_id"], second["job_id"]])

        self.assertEqual(job_status(client, second["job_id"])["position"], 2)
        saved = json.loads((self.harness.downloads / ".jobs.json").read_text())
        self.assertEqual(saved["_queue"], [third["job_id"], second["job_id"]])

        # Subir otra vez al primero no hace nada
        response = client.post(
            f"/api/queue/{third['job_id']}/move", json={"direction": "up"}
        )
        self.assertEqual(response.get_json()["position"], 1)

        # Bajar al último no hace nada
        response = client.post(
            f"/api/queue/{second['job_id']}/move", json={"direction": "down"}
        )
        self.assertEqual(response.get_json()["position"], 2)

        # B sube y vuelve a quedar por delante de C
        response = client.post(
            f"/api/queue/{second['job_id']}/move", json={"direction": "up"}
        )
        self.assertEqual(response.get_json()["queue"], [second["job_id"], third["job_id"]])

        # Validaciones
        self.assertEqual(
            client.post(
                f"/api/queue/{third['job_id']}/move", json={"direction": "lateral"}
            ).status_code,
            400,
        )
        self.assertEqual(
            client.post(
                f"/api/queue/{first['job_id']}/move", json={"direction": "up"}
            ).status_code,
            409,
        )
        self.assertEqual(
            client.post("/api/queue/no-existe/move", json={"direction": "up"}).status_code,
            404,
        )

        # El orden nuevo se respeta al arrancar el siguiente
        client.post(f"/api/cancel/{first['job_id']}")
        wait_for_status(client, second["job_id"], "downloading")
        self.assertEqual(job_status(client, third["job_id"])["status"], "queued")

        client.post(f"/api/cancel/{second['job_id']}")
        client.post(f"/api/cancel/{third['job_id']}")
        wait_until_idle(client)


class RateLimitTest(unittest.TestCase):
    def test_limit_rate_is_passed_to_ytdlp(self):
        harness = AppHarness(env={"MAX_DOWNLOAD_RATE": "5M"})
        harness.start()
        try:
            client = harness.client
            job = post_download(client, "https://example.com/a", "A")
            wait_for_status(client, job["job_id"], "done")

            call = harness.ytdlp_calls()[-1]
            self.assertIn("--limit-rate", call)
            self.assertEqual(call[call.index("--limit-rate") + 1], "5M")
        finally:
            harness.stop()

    def test_no_rate_limit_by_default(self):
        harness = AppHarness()
        harness.start()
        try:
            client = harness.client
            job = post_download(client, "https://example.com/a", "A")
            wait_for_status(client, job["job_id"], "done")
            self.assertNotIn("--limit-rate", harness.ytdlp_calls()[-1])
        finally:
            harness.stop()


if __name__ == "__main__":
    unittest.main()
