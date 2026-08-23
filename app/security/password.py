import bcrypt
import secrets
import string

def get_password_hash(password: str) -> str:
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(password=pwd_bytes, salt=salt)
    return hashed_password.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not plain_password or not hashed_password:
        return False
    try:
        password_byte_enc = plain_password.encode('utf-8')
        hashed_password_byte_enc = hashed_password.encode('utf-8')
        return bcrypt.checkpw(password_byte_enc, hashed_password_byte_enc)
    except Exception:
        return False

def generate_temporary_password(length: int = 12) -> str:
    """Generate a secure, randomized temporary password."""
    if length < 8:
        length = 8
    
    uppercase = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    lowercase = "abcdefghijkmnopqrstuvwxyz"
    digits = "23456789"
    special = "!@#$%&*+"
    all_chars = uppercase + lowercase + digits + special
    
    password = [
        secrets.choice(uppercase),
        secrets.choice(lowercase),
        secrets.choice(digits),
        secrets.choice(special)
    ]
    
    for _ in range(length - len(password)):
        password.append(secrets.choice(all_chars))
        
    secrets.SystemRandom().shuffle(password)
    return "".join(password)

