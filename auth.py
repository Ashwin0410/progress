import bcrypt
from sqlalchemy.orm import Session
from models import User


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))


def create_user(db: Session, email: str, password: str, name: str = "") -> User:
    user = User(email=email.lower().strip(), password_hash=hash_password(password), name=name.strip())
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str):
    user = db.query(User).filter(User.email == email.lower().strip()).first()
    if user and verify_password(password, user.password_hash):
        return user
    return None


def get_user_by_id(db: Session, user_id: int):
    return db.query(User).filter(User.id == user_id).first()
