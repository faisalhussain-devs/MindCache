from sqlalchemy import create_engine, text

def migrate():
    engine = create_engine('sqlite:///mindcache.db')
    with engine.connect() as conn:
        try:
            # Check if column exists
            result = conn.execute(text("PRAGMA table_info(topics)")).fetchall()
            columns = [r[1] for r in result]
            
            if 'description' not in columns:
                print("Adding 'description' column...")
                conn.execute(text("ALTER TABLE topics ADD COLUMN description TEXT"))
                conn.commit()
            else:
                print("'description' column already exists.")
                
            if 'timestamp' not in columns:
                 print("Adding 'timestamp' column...")
                 conn.execute(text("ALTER TABLE topics ADD COLUMN timestamp FLOAT"))
                 conn.commit()

            print("Schema check complete.")
        except Exception as e:
            print(f"Migration error: {e}")

if __name__ == "__main__":
    migrate()
