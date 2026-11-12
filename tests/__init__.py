import os
import tempfile

os.environ.setdefault("ONIGIRI_EXTENSIONS", tempfile.mkdtemp(prefix="onigiri-ext-"))
