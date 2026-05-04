from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Resident(db.Model):
    __tablename__ = "residents"

    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    barangay_id = db.Column(db.Integer, nullable=False)
    barangay_name = db.Column(db.String(100), nullable=False)  # helpful for filtering
    fcm_token = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Schedule(db.Model):
    __tablename__ = "schedules"

    id = db.Column(db.Integer, primary_key=True)
    barangay_id = db.Column(db.Integer, nullable=False)
    barangay_name = db.Column(db.String(100), nullable=False)
    collection_date = db.Column(db.Date, nullable=False)
    collection_time = db.Column(db.Time, nullable=False)
    waste_type = db.Column(db.String(50), default="household")
    notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default="scheduled")  # scheduled/done/cancelled
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Announcement(db.Model):
    __tablename__ = "announcements"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    target_barangay_id = db.Column(db.Integer, nullable=True)   # null = broadcast to all
    target_barangay_name = db.Column(db.String(100), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)