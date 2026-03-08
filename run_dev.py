import os
from app import create_app
from app.extensions import db

# Create the application instance
app = create_app('development')

if __name__ == '__main__':
    # Creación automática de tablas si no existen
    with app.app_context():
        try:
            db.create_all()
            print("✅ Tablas creadas/verificadas")
        except Exception as e:
            print(f"Error creando tablas: {e}")
            
    # Run the app
    # Port 5001 is used because macOS AirPlay Receiver often uses 5000
    app.run(host='0.0.0.0', port=5001, debug=True)
