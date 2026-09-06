"""Lifetime operating allowance, separate from GPU reservations.

Charge whole hours before running them, including cold starts. The rate bounds
0.5 CPU cores, 1 GiB RAM, and 10 GiB storage at the pinned deployment resources.
Neither restarts nor recurring monthly Modal credits replenish this allowance.
"""
import math
import threading
import time
import logging

log = logging.getLogger(__name__)


class OperatingBudget:
    def __init__(self, store, *, cap_usd=30, hourly_usd=.05):
        self.store, self.rate = store, math.ceil(hourly_usd * 1_000_000)
        self.cap = math.floor(cap_usd * 1_000_000)
        with store.transaction() as db:
            db.execute("INSERT OR IGNORE INTO settings VALUES ('operations_cap',?)", (str(self.cap),))
            db.execute("INSERT OR IGNORE INTO settings VALUES ('operations_spent','0')")
            db.execute("INSERT OR IGNORE INTO settings VALUES ('operations_paid_until','0')")

    def reserve_hour(self):
        with self.store.transaction() as db:
            settings = dict(db.execute("SELECT key,value FROM settings WHERE key LIKE 'operations_%'"))
            # Close admission one day before grant expiry so retention cleanup
            # can finish while the original credits are still valid.
            if self.store.clock() >= self.store.policy.expires_at - 86460:
                return False
            if self.store.clock() < float(settings['operations_paid_until']):
                return True
            if self.store.clock() >= self.store.policy.expires_at:
                return False
            spent, cap = int(settings['operations_spent']), min(self.cap, int(settings['operations_cap']))
            if spent + self.rate > cap:
                return False
            db.execute("UPDATE settings SET value=? WHERE key='operations_spent'", (str(spent + self.rate),))
            db.execute("UPDATE settings SET value=? WHERE key='operations_paid_until'", (str(self.store.clock() + 3600),))
            return True

    def stop_unfunded(self, service, shutdown, *, wait=time.sleep):
        """Stop compute, retain downloads until expiry, then stop the deployment.

        Deployment policy keeps $2 outside this hourly allowance for up to
        24 hours of cleanup at the bounded CPU/memory/storage settings. This
        deadline is durable and is never reset by a restart.
        """
        try:
            try:
                service.store.drain()
            finally:
                service.stop()
            service.store.cancel_pending()
            with self.store.transaction() as db:
                db.execute("INSERT OR IGNORE INTO settings VALUES ('retention_shutdown_at',?)", (str(self.store.clock() + 86460),))
                deadline = float(db.execute("SELECT value FROM settings WHERE key='retention_shutdown_at'").fetchone()[0])
            while True:
                self.store.cleanup()
                with self.store.lock:
                    retained = self.store.db.execute("SELECT COUNT(*) FROM inputs").fetchone()[0]
                    retained += self.store.db.execute("SELECT COUNT(*) FROM jobs WHERE result IS NOT NULL").fetchone()[0]
                if not retained or self.store.clock() >= deadline:
                    break
                wait(min(30, max(0, deadline - self.store.clock())))
        except Exception:
            log.exception("Retention cleanup failed; stopping compute and deployment")
        finally:
            shutdown()

    def monitor(self, service, shutdown, *, interval=30):
        """Fail closed and stop the deployment before a new unpaid hour begins."""
        def run():
            while not service.stopping.wait(interval):
                try:
                    funded = self.reserve_hour()
                except Exception:
                    funded = False
                if not funded:
                    self.stop_unfunded(service, shutdown)
                    return
        thread = threading.Thread(target=run, name="operating-budget", daemon=True)
        thread.start()
        return thread
