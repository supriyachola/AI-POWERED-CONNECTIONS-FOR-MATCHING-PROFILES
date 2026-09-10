import json
from app.database import Base, engine, SessionLocal, migrate_schema
from app.models import User, Profile
from app.auth import hash_password

migrate_schema()

demo_users = [
    ("arjun", "I love story games, travel, photography and building things.", "friendship and people to explore ideas with", ["gaming", "travel", "photography", "technology"], 24, "male", "India", "Karnataka", "Bengaluru", "Curious"),
    ("priya", "I enjoy anime, books, cafes, movies and weekend trips.", "new friends and interesting conversations", ["anime", "books", "travel", "movies", "food"], 23, "female", "India", "Maharashtra", "Mumbai", "Chill"),
    ("rahul", "Football, gaming, music and road trips are my thing.", "people to play and hang out with", ["sports", "gaming", "music", "travel"], 26, "male", "India", "Karnataka", "Mysuru", "Funny"),
    ("maya", "I paint, play guitar and love discovering local food.", "creative people and cultural experiences", ["art", "music", "food", "travel"], 25, "female", "India", "Tamil Nadu", "Chennai", "Creative"),
]

db = SessionLocal()
try:
    for username, bio, looking, interests, age, gender, country, state, city, vibe in demo_users:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            user = User(username=username, password_hash=hash_password("password123"), account_type="registered")
            db.add(user); db.flush(); db.add(Profile(user_id=user.id))
        profile = db.query(Profile).filter(Profile.user_id == user.id).first()
        profile.bio=bio; profile.looking_for=looking; profile.interests=json.dumps(interests); profile.age=age; profile.gender=gender; profile.country=country; profile.state=state; profile.city=city; profile.vibe=vibe
        profile.preferred_gender="any"; profile.preferred_country="any"; profile.preferred_state="any"; profile.min_age=18; profile.max_age=100
    db.commit(); print("Seed complete. Demo password for all users: password123")
finally:
    db.close()

