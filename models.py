from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean, Float, Enum as SqEnum
from sqlalchemy.orm import relationship
from datetime import datetime, timedelta
import enum
from database import Base 

# --- Enums ---
class UserRole(enum.Enum):
    MASTER = "master"
    TECHNICIAN_MANAGER = "technician_manager"
    TECHNICIAN = "technician"
    MANAGER = "manager"
    USER = "user"

class TicketStatus(enum.Enum):
    OPEN = "open"
    CLOSED = "closed"

# ==============================================================
# 1. טבלאות תשתית (Catalog & Dictionaries) - דרישות 3, 4, 6, 7
# ==============================================================
class CatalogItem(Base): 
    __tablename__ = 'catalog_items'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False) # דוגמה: "מכשיר קשר 710"
    check_frequency_hours = Column(Integer, default=24) # דרישה: מחזורי דיווח משתנים (ברירת מחדל 24 שעות)

class FaultType(Base): 
    __tablename__ = 'fault_types'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False) # דוגמה: "מסך שבור"
    is_pending = Column(Boolean, default=False)
    requested_by_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    requester = relationship("User", foreign_keys=[requested_by_id])

class SolutionType(Base): 
    __tablename__ = 'solution_types'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False) # דוגמה: "החלפת אנטנה"

# ===============================================
# 2. ישויות ליבה (User, Location) - דרישות 1, 2
# ===============================================
class Location(Base):
    __tablename__ = 'locations'
    id = Column(Integer, primary_key=True, index=True)
    # שמרנו על כל השדות המקוריים שלך:
    battalion = Column(String, nullable=False)
    company = Column(String, nullable=False)
    post = Column(String, nullable=False)     # <-- הוחזר!
    location_name = Column(String, nullable=False)

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, index=True)
    personal_number = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String, nullable=False)
    battalion = Column(String, nullable=False)
    company = Column(String, nullable=False)
    
    # אבטחה ולוגיקה (נשמר מהקוד המקורי)
    password_hash = Column(String, nullable=False) # <-- הוחזר!
    role = Column(SqEnum(UserRole), default=UserRole.USER)
    is_active_duty = Column(Boolean, default=True) # סטטוס פעיל/לא פעיל

    


    # Matrix Security Fields
    profile_id = Column(Integer, ForeignKey('profiles.id'), nullable=True)
    unit_path = Column(String, nullable=True) # e.g. "Brigade1/Bat101/Co_A"

    profile = relationship("Profile")

# =======================================
# 3. הציוד (Equipment) - הלב של המערכת
# =======================================
class Equipment(Base):
    __tablename__ = 'equipment'
    
    id = Column(Integer, primary_key=True, index=True) # דרישה 8 (מזהה ייחודי)
    serial_number = Column(String, unique=True, nullable=True) # The "Tsadiq"
    
    # חיבור לקטלוג החדש (במקום טקסט חופשי) - דרישה 3
    catalog_item_id = Column(Integer, ForeignKey('catalog_items.id'), nullable=False)
    status = Column(String, default="Functional") 
    
    # Matrix Security Fields
    sensitivity = Column(String, default="UNCLASSIFIED") # CLASSIFIED / UNCLASSIFIED
    unit_hierarchy = Column(String, nullable=True) # e.g. "Brigade1/Bat101" - Denormalized for fast filtering 
    
    # --- מנגנון בעלות מול החזקה (מהקוד המקורי) ---
    owner_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    owner_location_id = Column(Integer, ForeignKey('locations.id'), nullable=True) # דרישה: שיוך למקום (מחסן/חמ"ל)
    holder_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    actual_location_id = Column(Integer, ForeignKey('locations.id'), nullable=True)

    # מתי ראינו את הציוד לאחרונה? - דרישה 9
    last_verified_at = Column(DateTime, default=datetime.utcnow)

    # קשרים (Relationships)
    catalog_item = relationship("CatalogItem")
    owner = relationship("User", foreign_keys=[owner_user_id])
    owner_location = relationship("Location", foreign_keys=[owner_location_id])
    holder = relationship("User", foreign_keys=[holder_user_id])
    location = relationship("Location", foreign_keys=[actual_location_id])

    # --- הפונקציות החכמות (נשמרו!) ---
    @property
    def item_name(self):
        """מחזיר את שם הציוד מהקטלוג"""
        return self.catalog_item.name if self.catalog_item else "Unknown"

    @property
    def current_state_description(self):
        """לוגיקה חכמה: בעלים מול מחזיק"""
        location_desc = "לא ידוע"
        
        # 1. איפה זה פיזית?
        if self.holder_user_id:
            # אם יש מחזיק, נבדוק בזהירות אם הוא קיים (לפעמים בטעינה ראשונית יש לאגים)
            holder_name = self.holder.full_name if self.holder else "Unknown"
            location_desc = f"אצל {holder_name}"
        elif self.actual_location_id:
            loc_name = self.location.location_name if self.location else "Unknown"
            location_desc = f"ב-{loc_name}"
            
        # 2. האם יש בעלים רשמי?
        if self.owner_user_id:
            owner_name = self.owner.full_name if self.owner else "Unknown"
            
            # חריגה: הציוד אצל מישהו אחר שאינו הבעלים
            if self.holder_user_id and self.holder_user_id != self.owner_user_id:
                holder_name = self.holder.full_name if self.holder else "Unknown"
                return f"שייך ל{owner_name}, אבל נמצא פיזית אצל {holder_name}"
            
            # הציוד במחסן
            if self.actual_location_id:
                loc_name = self.location.location_name if self.location else "Unknown"
                return f"שייך ל{owner_name}, מאוחסן ב{loc_name}"
            
            return f"בשימוש שוטף אצל {owner_name}"
            
        return f"במלאי ללא בעלים (יתום), כרגע: {location_desc}"

    @property
    def compliance_level(self):
        """
        Returns UI status color: GOOD (Green), WARNING (Yellow), SEVERE (Red)
        """
        if not self.last_verified_at:
            return "SEVERE"
        
        diff = datetime.utcnow() - self.last_verified_at
        if diff < timedelta(hours=24):
            return "GOOD"
        elif diff < timedelta(hours=48):
            return "WARNING"
        else:
            return "SEVERE"

    @property
    def report_status(self):
        """בדיקת חריגת דיווח (Heartbeat)"""
        if not self.last_verified_at:
            return "מעולם לא דווח"
        
        time_diff = datetime.utcnow() - self.last_verified_at
        if time_diff > timedelta(hours=24):
            return f"חריגת דיווח! עברו {time_diff.days} ימים ו-{int(time_diff.seconds/3600)} שעות"
        return "דיווח תקין"

# =======================================
# 4. היסטוריה וזיכרון - דרישות 5, 9, 10
# =======================================

# א. היסטוריית תנועות כללית (נשמר מהקוד המקורי)
class TransactionLog(Base):
    __tablename__ = 'transaction_logs'
    id = Column(Integer, primary_key=True, index=True)
    equipment_id = Column(Integer, ForeignKey('equipment.id'))
    involved_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    involved_location_id = Column(Integer, ForeignKey('locations.id'), nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    user_status_at_time = Column(Boolean, nullable=True) # הזיכרון ל-AI (האם היה פעיל?)
    event_type = Column(String) 
    
    # שדות לתקלות פשוטות (אופציונלי, למקרה שנרצה תאימות אחורה)
    is_returned_broken = Column(Boolean, default=False)
    broken_description = Column(String, nullable=True)

# ב. יומן משמעת ודיווחים (חדש! דרישה 9, 10)
class ComplianceLog(Base): 
    __tablename__ = 'compliance_logs'
    id = Column(Integer, primary_key=True, index=True)
    equipment_id = Column(Integer, ForeignKey('equipment.id'))
    responsible_user_id = Column(Integer, ForeignKey('users.id')) # מי המחזיק שהיה צריך לדווח
    timestamp = Column(DateTime, default=datetime.utcnow)
    reported_on_time = Column(Boolean, default=True)
    delay_hours = Column(Float, default=0.0) # כמה שעות איחור נרשמו לחובתו

# ג. יומן תחזוקה טכני (חדש! דרישות 4, 5, 6, 7)
class MaintenanceLog(Base):
    __tablename__ = 'maintenance_logs'
    id = Column(Integer, primary_key=True, index=True)
    equipment_id = Column(Integer, ForeignKey('equipment.id'))
    
    # תיקון: הפכנו ל-Nullable כדי שמשתמש יוכל לפתוח תקלה בלי לאבחן אותה
    fault_type_id = Column(Integer, ForeignKey('fault_types.id'), nullable=True) 
    solution_type_id = Column(Integer, ForeignKey('solution_types.id'), nullable=True)
    
    description = Column(String) 
    occurred_at_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)

    # Ticket System fields
    status = Column(SqEnum(TicketStatus), default=TicketStatus.OPEN)
    closed_at = Column(DateTime, nullable=True)
    closed_by_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    is_false_alarm = Column(Boolean, default=False)
    
    # הערות טכנאי (הוספתי את זה כי זה קריטי לניהול תקלה)
    tech_notes = Column(String, nullable=True)

# ד. ביקורת מלאי יומית (חדש! דרישה לדוח תנועות)
class InventoryAudit(Base):
    __tablename__ = 'inventory_audits'
    id = Column(Integer, primary_key=True, index=True)
    equipment_id = Column(Integer, ForeignKey('equipment.id'))
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    # Snapshot fields
    recorded_status = Column(String)
    location_id = Column(Integer, ForeignKey('locations.id'), nullable=True)
    owner_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    holder_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)



# ו. פרופיל אבטחה מטריציוני (Matrix Security Profile)
class Profile(Base):
    __tablename__ = 'profiles'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False) # e.g. "Company Commander"
    name_he = Column(String, nullable=True) # Hebrew Display Name
    
    # --- Report Permissions ---
    can_generate_battalion_report = Column(Boolean, default=False)
    can_generate_company_report   = Column(Boolean, default=False)
    
    # --- Realtime View Permissions ---
    can_view_battalion_realtime   = Column(Boolean, default=False)
    can_view_company_realtime     = Column(Boolean, default=False)
    
    # --- Action Permissions ---
    can_change_assignment_others  = Column(Boolean, default=False) # Change owner/holder
    can_change_maintenance_status = Column(Boolean, default=False) # Fix/Break items
    
    can_manage_locations          = Column(Boolean, default=False) # Add/Edit Locations
    
    # --- Catalog & Inventory Permissions ---
    can_add_category              = Column(Boolean, default=False) # Add Catalog Item
    can_add_specific_item         = Column(Boolean, default=False) # Add Specific Item
    
    can_remove_category           = Column(Boolean, default=False) 
    can_remove_specific_item      = Column(Boolean, default=False) 
    
    # --- Basic Duties ---
    holds_equipment               = Column(Boolean, default=True)
    must_report_presence          = Column(Boolean, default=True)
    
    # --- Admin ---
    can_assign_roles              = Column(Boolean, default=False) # Master only