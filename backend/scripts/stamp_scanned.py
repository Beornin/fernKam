import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import psycopg2
from fernkam.config import get_settings

s = get_settings()
url = s.pg_url_sync.replace('postgresql+psycopg2://', 'postgresql://')
conn = psycopg2.connect(url)
cur = conn.cursor()
cur.execute(
    "UPDATE photos SET faces_scanned_at = NOW() "
    "WHERE id IN (SELECT DISTINCT photo_id FROM faces) "
    "AND faces_scanned_at IS NULL"
)
print(f'Stamped {cur.rowcount} photos with faces as scanned')
conn.commit()
conn.close()
