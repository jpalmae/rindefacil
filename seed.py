from app import create_app
from app.extensions import db
from app.models.company import Company
from app.models.user import User, UserRole
from app.models.category import Category

app = create_app('development')

def seed_database():
    with app.app_context():
        # Clean current DB
        db.drop_all()
        db.create_all()
        print("Tablas recreadas.")

        # Create Company
        demo_company = Company(name="Demo Corp", rut="12345678-9")
        db.session.add(demo_company)
        db.session.flush()

        # Create Categories
        cats = ["Viajes", "Alimentación", "Hospedaje", "Suministros"]
        for c in cats:
            db.session.add(Category(company_id=demo_company.id, name=c))
        
        # Create Admin User
        admin = User(
            company_id=demo_company.id,
            email="admin@demo.com",
            full_name="Admin Principal",
            role=UserRole.ADMIN
        )
        admin.set_password("admin123")
        db.session.add(admin)

        # Create Employee User
        employee = User(
            company_id=demo_company.id,
            email="user@demo.com",
            full_name="Usuario Prueba",
            role=UserRole.EMPLOYEE
        )
        employee.set_password("user123")
        db.session.add(employee)

        db.session.commit()
        print("✅ Base de datos poblada exitosamente.")
        print("Puedes iniciar sesión con: admin@demo.com / admin123")

if __name__ == '__main__':
    seed_database()
