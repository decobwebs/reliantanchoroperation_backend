from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from app.models.audit import SystemSetting


async def _next_sequence(db: AsyncSession, key: str, description: str) -> int:
    """Atomically return the next value for a named counter in system_settings.

    A transaction-level Postgres advisory lock keyed on the sequence name serializes
    concurrent callers — this closes the first-insert race that SELECT ... FOR UPDATE
    alone cannot cover (FOR UPDATE only locks an *existing* row, so two concurrent
    "first of the year" inserts would otherwise both produce sequence value 1).
    """
    # Serialize on this counter key for the rest of the transaction.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})

    stmt = select(SystemSetting).where(SystemSetting.key == key).with_for_update()
    setting = (await db.execute(stmt)).scalar_one_or_none()

    if setting is None:
        current = 1
        db.add(SystemSetting(key=key, value={"current": current}, description=description))
    else:
        current = setting.value.get("current", 0) + 1
        setting.value = {"current": current}

    await db.flush()
    return current


async def generate_operation_number(db: AsyncSession) -> str:
    """Generate a sequential operation number like RA-2026-0001."""
    year = datetime.utcnow().year
    n = await _next_sequence(db, f"seq_operation_{year}", f"Operation sequence counter for {year}")
    return f"RA-{year}-{n:04d}"


async def generate_bdn_number(db: AsyncSession) -> str:
    """Generate a globally sequential BDN number like BDN-000001."""
    n = await _next_sequence(db, "seq_bdn_global", "Global BDN sequence counter")
    return f"BDN-{n:06d}"


async def generate_truck_bdn_number(db: AsyncSession) -> str:
    """Generate a globally sequential Truck BDN number like TBDN-000001."""
    n = await _next_sequence(db, "seq_truck_bdn_global", "Global Truck BDN sequence counter")
    return f"TBDN-{n:06d}"


async def generate_pfi_number(db: AsyncSession) -> str:
    """Generate a yearly sequential PFI number like PFI-2026-0001."""
    year = datetime.utcnow().year
    n = await _next_sequence(db, f"seq_pfi_{year}", f"PFI sequence counter for {year}")
    return f"PFI-{year}-{n:04d}"


async def generate_voucher_number(db: AsyncSession) -> str:
    """Generate a yearly sequential voucher number like VCH-2026-0001."""
    year = datetime.utcnow().year
    n = await _next_sequence(db, f"seq_voucher_{year}", f"Voucher sequence counter for {year}")
    return f"VCH-{year}-{n:04d}"


async def generate_invoice_number(db: AsyncSession) -> str:
    """Generate a yearly sequential invoice number like INV-2026-0001."""
    year = datetime.utcnow().year
    n = await _next_sequence(db, f"seq_invoice_{year}", f"Invoice sequence counter for {year}")
    return f"INV-{year}-{n:04d}"


async def generate_vessel_activity_number(db: AsyncSession) -> str:
    """Generate a yearly sequential vessel activity number like VA-2026-0001."""
    year = datetime.utcnow().year
    n = await _next_sequence(db, f"seq_vessel_activity_{year}", f"Vessel activity sequence counter for {year}")
    return f"VA-{year}-{n:04d}"


async def generate_expense_voucher_number(db: AsyncSession) -> str:
    """Generate a yearly sequential expense voucher number like EXP-2026-0001."""
    year = datetime.utcnow().year
    n = await _next_sequence(db, f"seq_expense_{year}", f"Expense voucher sequence counter for {year}")
    return f"EXP-{year}-{n:04d}"
