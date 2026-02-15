from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext

import os
from dotenv import load_dotenv

load_dotenv()

# --- Configuration ---
# In a real production app, retrieving these from environment variables is crucial.
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("No SECRET_KEY set for FastAPI application") 
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# --- Password Hashing ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

# --- JWT Token ---
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# --- Matrix Security Logic ---
import json
from . import models


def get_visible_equipment(user: models.User, initial_query):
    """
    Applies Green Table RBAC Filters
    Returns: Filtered Query
    """
    if user.role == models.UserRole.MASTER or (user.profile and user.profile.name == "Master"):
        return initial_query



    if not user.profile:
        # Default fallback: Personal Only
        return initial_query.filter(models.Equipment.holder_user_id == user.id)

    # 1. Broader Scope First: Battalion View
    if user.profile.can_view_battalion_realtime:
        if user.unit_path:
             # Assuming unit_path for Bat Cmdr is "Brigade1/Bat1"
             search_path = f"{user.unit_path}%"
             return initial_query.filter(models.Equipment.unit_hierarchy.like(search_path))
        else:
             # Fallback if path missing but perm exists - maybe fallback to Battalion field on User?
             # For this task, we assume unit_path is the source of truth for hierarchy.
             pass

    # 2. Company View
    if user.profile.can_view_company_realtime:
        if user.unit_path:
             search_path = f"{user.unit_path}%"
             return initial_query.filter(models.Equipment.unit_hierarchy.like(search_path))

    # 3. Personal View (Default if no broader view perm)
    return initial_query.filter(models.Equipment.holder_user_id == user.id)
