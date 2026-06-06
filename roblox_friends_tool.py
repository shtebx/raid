import requests
import json
import uuid
import sys
import time
import concurrent.futures

sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None

JSON_FILE = "requests.json"
MAX_THREADS = 1

class RobloxFriendsTool:
    def __init__(self, roblosecurity: str, browser_tracker_id: str | None = None):
        self.session = requests.Session()
        self.session.cookies.set(".ROBLOSECURITY", roblosecurity, domain=".roblox.com")

        tracker = browser_tracker_id or str(uuid.uuid4())
        self.session.cookies.set("RBXBrowserTrackerID", tracker, domain=".roblox.com")

        self.csrf_token = None
        self._get_csrf()
        print("[OK] Session initialized")

    # --------------------------------------------------
    def _get_csrf(self):
        r = self.session.post(
            "https://auth.roblox.com/v2/logout",
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        token = r.headers.get("x-csrf-token")
        if not token:
            raise RuntimeError("Failed to get CSRF token")
        self.csrf_token = token

    # --------------------------------------------------
    def _post_with_retry(self, url, max_retries=5):
        """POST запрос с автоматическим retry при 429 и обновлением CSRF при 403"""
        for attempt in range(max_retries):
            r = self.session.post(
                url,
                headers={"x-csrf-token": self.csrf_token},
                timeout=10,
            )
            if r.status_code == 200:
                return True
            elif r.status_code == 403:
                new_token = r.headers.get("x-csrf-token")
                if new_token:
                    self.csrf_token = new_token
                # retry immediately
            elif r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 10))
                print(f"[RATE LIMIT] 429 — waiting {wait}s... (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
            else:
                print(f"[WARN] Unexpected status {r.status_code} for {url}")
                return False
        return False

    # --------------------------------------------------
    def get_user_id(self):
        r = self.session.get(
            "https://users.roblox.com/v1/users/authenticated",
            timeout=10,
        )
        if r.status_code != 200:
            raise RuntimeError(f"Failed to get user ID: HTTP {r.status_code}")
        return r.json()["id"]

    # --------------------------------------------------
    def get_friend_requests(self):
        print("[...] Loading friend requests...")
        all_ids = []
        cursor = None

        while True:
            r = self.session.get(
                "https://friends.roblox.com/v1/my/friends/requests",
                params={"limit": 100, "cursor": cursor},
                headers={"x-csrf-token": self.csrf_token},
                timeout=10,
            )
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")

            data = r.json()
            for u in data.get("data", []):
                all_ids.append(u["id"])

            cursor = data.get("nextPageCursor")
            if not cursor:
                break

        print(f"[OK] Found {len(all_ids)} requests")
        return all_ids

    # --------------------------------------------------
    def get_friends_list(self):
        print("[...] Loading friends list...")
        user_id = self.get_user_id()

        r = self.session.get(
            f"https://friends.roblox.com/v1/users/{user_id}/friends",
            timeout=10,
        )
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")

        all_ids = [u["id"] for u in r.json().get("data", [])]
        print(f"[OK] Found {len(all_ids)} friends")
        return all_ids

    # --------------------------------------------------
    def save_ids_to_json(self):
        ids = self.get_friend_requests()
        with open(JSON_FILE, "w", encoding="utf-8") as f:
            json.dump({"ids": ids}, f, indent=2)
        print(f"[OK] Saved {len(ids)} IDs to {JSON_FILE}")

    # --------------------------------------------------
    def accept_request(self, user_id: int) -> bool:
        try:
            return self._post_with_retry(
                f"https://friends.roblox.com/v1/users/{user_id}/accept-friend-request"
            )
        except Exception as e:
            print(f"[ERR] accept {user_id}: {e}")
            return False

    # --------------------------------------------------
    def decline_request(self, user_id: int) -> bool:
        try:
            return self._post_with_retry(
                f"https://friends.roblox.com/v1/users/{user_id}/decline-friend-request"
            )
        except Exception as e:
            print(f"[ERR] decline {user_id}: {e}")
            return False

    # --------------------------------------------------
    def unfriend(self, user_id: int) -> bool:
        try:
            return self._post_with_retry(
                f"https://friends.roblox.com/v1/users/{user_id}/unfriend"
            )
        except Exception as e:
            print(f"[ERR] unfriend {user_id}: {e}")
            return False

    # --------------------------------------------------
    def accept_all_except_json(self):
        try:
            with open(JSON_FILE, "r", encoding="utf-8") as f:
                ignored = set(json.load(f).get("ids", []))
        except FileNotFoundError:
            ignored = set()

        ids = self.get_friend_requests()
        accepted = 0
        skipped = 0

        def worker(uid):
            if uid in ignored:
                return "skipped"
            return "accepted" if self.accept_request(uid) else "failed"

        print(f"[INFO] Accepting requests with {MAX_THREADS} threads...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
            results = list(executor.map(worker, ids))

        for res in results:
            if res == "accepted":
                accepted += 1
            elif res == "skipped":
                skipped += 1

        print("=" * 60)
        print(f"[OK] Accepted: {accepted}")
        print(f"[OK] Skipped : {skipped}")
        print("=" * 60)

# ======================================================
def main():
    print("=" * 60)
    print("Roblox Friends Tool (Fast Accept)")
    print("=" * 60)

    cookie = input("Enter .ROBLOSECURITY: ").strip()
    tracker = input("RBXBrowserTrackerID (optional): ").strip() or None

    print("\nOPTIONS:")
    print("1 - Save friend request IDs to JSON")
    print("2 - Accept all requests (ignore JSON IDs)")

    choice = input("Select (1/2): ").strip()
    tool = RobloxFriendsTool(cookie, tracker)

    if choice == "1":
        tool.save_ids_to_json()
    elif choice == "2":
        tool.accept_all_except_json()
    else:
        print("Invalid option")

if __name__ == "__main__":
    main()
