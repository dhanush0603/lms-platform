import sqlite3
from werkzeug.security import generate_password_hash

# --- DATABASE INITIALIZATION ---

# Connect to (or create) the database file
conn = sqlite3.connect('lms.db')
cursor = conn.cursor()

# --- CREATE USERS TABLE ---
# This table will store user details and their role (admin or intern)
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password TEXT,
        role TEXT NOT NULL CHECK(role IN ('admin', 'intern')),
        status TEXT NOT NULL DEFAULT 'pending'
    );
''')
print("✅ 'users' table created or already exists.")

cursor.execute('''
    CREATE TABLE IF NOT EXISTS password_reset_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token TEXT NOT NULL UNIQUE,
        expires_at DATETIME NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id)
    );
''')
print("✅ 'password_reset_tokens' table created or already exists.")


# --- CREATE COURSES TABLE ---
# This table will store the course details uploaded by the admin
cursor.execute('''
    CREATE TABLE IF NOT EXISTS courses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        filename TEXT NOT NULL,
        extracted_text TEXT 
    );
''')

# Find this table definition...
cursor.execute('''
    CREATE TABLE IF NOT EXISTS quiz_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        course_id INTEGER NOT NULL,
        quiz_data TEXT NOT NULL,
        score INTEGER,
        total_questions INTEGER,
        status TEXT, 
        attempted_at DATETIME NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id),
        FOREIGN KEY (course_id) REFERENCES courses (id)
    );
''')

print("✅ 'courses' table created or already exists.")
# --- INITIAL ADMIN CREATION ---
# We will create an initial admin user only if one doesn't already exist.
# This prevents creating duplicate admins every time the script is run.

try:
    # Hash the password for security. NEVER store plain text passwords.
    # Replace 'AdminPassword123!' with a strong password of your choice.
    hashed_password = generate_password_hash('AdminPassword123!', method='pbkdf2:sha256')

    # The 'role' is explicitly set to 'admin'
    cursor.execute('''
    INSERT INTO users (name, email, password, role, status)
    VALUES (?, ?, ?, ?, ?)
''', ('Admin', 'admin@lms.com', hashed_password, 'admin', 'active'))

    
    print("✅ Initial admin user 'admin@lms.com' created successfully.")

except sqlite3.IntegrityError:
    # This error occurs if the email (which is UNIQUE) already exists.
    print("ℹ️ Initial admin user 'admin@lms.com' already exists.")


# --- COMMIT AND CLOSE ---
conn.commit()
conn.close()

print("\nDatabase setup complete!")