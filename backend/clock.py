*** Begin Patch
*** Update File: backend/clock.py
@@
 def iso_z(value: Optional[datetime]) -> Optional[str]:
-    """Serialize an aware (or naive-assumed-UTC) datetime as '...Z'.
-
-    For the two hand-built dicts in routers/reports.py only, which have no
-    response_model and so bypass Pydantic's own '...Z' serialization -- this
-    makes their output match every other endpoint's wire format instead of
-    emitting '+00:00' via jsonable_encoder.
-    """
-    if value is None:
-        return None
-    if value.tzinfo is None:
-        value = value.replace(tzinfo=timezone.utc)
-    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
+    """Serialize an aware (or naive-assumed-UTC) datetime to an ISO string.
+
+    Return an ISO 8601 string with an explicit offset ("+00:00") so Python's
+    datetime.fromisoformat can parse it without substitutions. Previously this
+    function replaced "+00:00" with "Z", which caused datetime.fromisoformat
+    to raise when tests parsed the wire value directly.
+    """
+    if value is None:
+        return None
+    if value.tzinfo is None:
+        value = value.replace(tzinfo=timezone.utc)
+    # Use the standard isoformat with explicit offset (e.g. "...+00:00").
+    return value.astimezone(timezone.utc).isoformat()
*** End Patch
