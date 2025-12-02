#!/usr/bin/env python3
"""
Script to update user to admin role
Run this from the terminal: python make_admin.py
"""

import sys
import os

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from models import db, User, UserRole


def make_user_admin(email):
    """Update a user to admin role"""
    app = create_app()

    with app.app_context():
        try:
            # Find the user by email
            user = User.query.filter_by(email=email).first()

            if not user:
                print(f"❌ User with email '{email}' not found!")
                return False

            print(f"📧 Found user: {user.name} ({user.email})")
            print(f"👤 Current role: {user.role.value}")

            # Confirm the change
            confirm = input(f"⚠️  Are you sure you want to make {user.email} an admin? (y/N): ")

            if confirm.lower() != 'y':
                print("❌ Operation cancelled.")
                return False

            # Update to admin
            user.role = UserRole.ADMIN
            db.session.commit()

            print(f"✅ Successfully updated {user.email} to ADMIN role!")
            print(f"👑 New role: {user.role.value}")
            return True

        except Exception as e:
            print(f"❌ Error: {e}")
            db.session.rollback()
            return False


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python make_admin.py <user_email>")
        print("Example: python make_admin.py john.doe@company.com")
        sys.exit(1)

    email = sys.argv[1]
    make_user_admin(email)