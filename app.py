import os
import time
import threading
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from werkzeug.utils import secure_filename
from icloud_fetcher import fetch_icloud_photos_selenium, get_local_photos

ICLOUD_ALBUM_URL = "https://www.icloud.com/sharedalbum/#B21G6XBub3ekWr"
SYNC_INTERVAL = 30  # seconds

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB max
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
db = SQLAlchemy(app)
CORS(app)

# Create upload folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Database model
class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)

class Photo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=db.func.current_timestamp())

@app.route('/api/items', methods=['GET'])
def get_items():
    items = Item.query.all()
    return jsonify([{'id': item.id, 'name': item.name} for item in items])

@app.route('/api/photos', methods=['GET'])
def get_photos():
    # Get uploaded photos from database
    uploaded_photos = Photo.query.order_by(Photo.uploaded_at.desc()).all()
    photos = []
    for photo in uploaded_photos:
        photos.append({
            'id': f'upload_{photo.id}',
            'filename': photo.filename,
            'original_filename': photo.original_filename,
            'src': f'/api/photos/{photo.filename}',
            'isUploaded': True
        })
    return jsonify(photos)

@app.route('/api/photos', methods=['POST'])
def upload_photo():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        # Add timestamp to filename to avoid conflicts
        timestamp = int(time.time() * 1000)
        name, ext = os.path.splitext(filename)
        safe_filename = f"{name}_{timestamp}{ext}"
        
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
        file.save(filepath)
        
        # Save to database
        photo = Photo(filename=safe_filename, original_filename=file.filename)
        db.session.add(photo)
        db.session.commit()
        
        return jsonify({
            'id': f'upload_{photo.id}',
            'filename': safe_filename,
            'original_filename': file.filename,
            'src': f'/api/photos/{safe_filename}',
            'isUploaded': True
        }), 201
    
    return jsonify({'error': 'File type not allowed'}), 400

@app.route('/api/photos/<filename>', methods=['GET'])
def serve_photo(filename):
    from flask import send_from_directory
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/photos/<filename>', methods=['GET'])
def serve_local_photo(filename):
    """Serve photos from the frontend public/photos directory"""
    from flask import send_from_directory
    photos_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'frontend', 'public', 'photos')
    if os.path.exists(photos_dir):
        return send_from_directory(photos_dir, filename)
    return jsonify({'error': 'Photo not found'}), 404

# ── iCloud Shared Album endpoints ──────────────────────────────────────────────

class iCloudAlbum(db.Model):
    """Store iCloud Shared Album configuration"""
    id = db.Column(db.Integer, primary_key=True)
    album_url = db.Column(db.String(500), nullable=False)
    last_fetched = db.Column(db.DateTime, default=db.func.current_timestamp())

class iCloudPhoto(db.Model):
    """Cache of photos fetched from iCloud Shared Album"""
    id = db.Column(db.Integer, primary_key=True)
    photo_id = db.Column(db.String(255), nullable=False, unique=True)
    filename = db.Column(db.String(255), nullable=False)
    thumbnail_url = db.Column(db.String(500))
    full_url = db.Column(db.String(500))
    fetched_at = db.Column(db.DateTime, default=db.func.current_timestamp())

@app.route('/api/icloud/album', methods=['GET'])
def get_icloud_album():
    """Get stored iCloud album configuration"""
    album = iCloudAlbum.query.first()
    if not album:
        return jsonify({'configured': False}), 200
    return jsonify({
        'configured': True,
        'album_url': album.album_url,
        'last_fetched': album.last_fetched.isoformat() if album.last_fetched else None
    }), 200

@app.route('/api/icloud/album', methods=['POST'])
def set_icloud_album():
    """Set iCloud Shared Album URL"""
    data = request.get_json()
    album_url = data.get('album_url', '').strip()
    
    if not album_url:
        return jsonify({'error': 'Album URL is required'}), 400
    
    # Basic validation for iCloud shared album URL
    if 'icloud.com/sharedalbum' not in album_url and 'icloud.com/sharedalbum/' not in album_url:
        return jsonify({'error': 'Invalid iCloud Shared Album URL'}), 400
    
    # Clear existing album config
    iCloudAlbum.query.delete()
    
    album = iCloudAlbum(album_url=album_url)
    db.session.add(album)
    db.session.commit()
    
    return jsonify({'success': True, 'album_url': album_url}), 201

def _sync_icloud_photos():
    """Fetch latest photos from iCloud and upsert into the database. Returns count."""
    album = iCloudAlbum.query.first()
    if not album:
        return 0
    photos = fetch_icloud_photos_selenium(album.album_url)
    if not photos:
        return 0
    for photo in photos:
        existing = iCloudPhoto.query.filter_by(photo_id=photo['id']).first()
        if existing:
            existing.thumbnail_url = photo.get('thumbnail_url', '')
            existing.full_url = photo.get('full_url', '')
        else:
            db.session.add(iCloudPhoto(
                photo_id=photo['id'],
                filename=photo.get('filename', ''),
                thumbnail_url=photo.get('thumbnail_url', ''),
                full_url=photo.get('full_url', '')
            ))
    db.session.commit()
    return len(photos)


def _background_sync():
    """Background thread: sync iCloud photos every SYNC_INTERVAL seconds."""
    while True:
        try:
            with app.app_context():
                count = _sync_icloud_photos()
                print(f"[sync] {count} photos synced")
        except Exception as e:
            print(f"[sync] Error: {e}")
        time.sleep(SYNC_INTERVAL)


@app.route('/api/icloud/fetch', methods=['POST'])
def fetch_icloud_photos():
    """Manually trigger an iCloud sync."""
    album = iCloudAlbum.query.first()
    if not album:
        return jsonify({'error': 'No album configured'}), 400
    try:
        count = _sync_icloud_photos()
        return jsonify({'success': True, 'photos_fetched': count}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/icloud/photos', methods=['GET'])
def get_icloud_photos():
    """Get cached iCloud photos"""
    photos = iCloudPhoto.query.order_by(iCloudPhoto.fetched_at.desc()).all()
    result = []
    for photo in photos:
        result.append({
            'id': photo.photo_id,
            'filename': photo.filename,
            'thumbnail_url': photo.thumbnail_url,
            'full_url': photo.full_url,
            'fetched_at': photo.fetched_at.isoformat() if photo.fetched_at else None
        })
    return jsonify(result), 200


@app.route('/api/notify/tree-died', methods=['POST'])
def notify_tree_died():
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    raw_ids   = os.environ.get('TELEGRAM_CHAT_IDS', '')

    if not all([bot_token, raw_ids]):
        return jsonify({'error': 'Telegram not configured'}), 503

    chat_ids = [c.strip() for c in raw_ids.split(',') if c.strip()]
    if not chat_ids:
        return jsonify({'error': 'No chat IDs configured'}), 503

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    msg = "🌳 Prachi's focus tree just died — she gave up or left the app. Hold her accountable! 💪"

    sent = 0
    for chat_id in chat_ids:
        try:
            resp = requests.post(url, json={"chat_id": chat_id, "text": msg}, timeout=10)
            if resp.status_code == 200:
                sent += 1
            else:
                print(f"[Telegram] Failed for {chat_id}: {resp.status_code} {resp.text}")
        except Exception as e:
            print(f"[Telegram] Error for {chat_id}: {e}")

    return jsonify({'success': True, 'notified': sent}), 200


@app.route('/api/icloud/clear', methods=['POST'])
def clear_icloud_data():
    """Clear iCloud album configuration and cached photos"""
    iCloudAlbum.query.delete()
    iCloudPhoto.query.delete()
    db.session.commit()
    return jsonify({'success': True}), 200


# ── Startup: runs under both gunicorn and python app.py ───────────────────────

with app.app_context():
    db.create_all()
    if not iCloudAlbum.query.first():
        db.session.add(iCloudAlbum(album_url=ICLOUD_ALBUM_URL))
        db.session.commit()
        print(f"[startup] Album configured: {ICLOUD_ALBUM_URL}")

_sync_started = False
_sync_lock = threading.Lock()

@app.before_request
def _start_sync_once():
    global _sync_started
    if not _sync_started:
        with _sync_lock:
            if not _sync_started:
                _sync_started = True
                threading.Thread(target=_background_sync, daemon=True).start()
                print(f"[startup] Background sync started (every {SYNC_INTERVAL}s)")


if __name__ == '__main__':
    app.run(debug=True, port=8080)
