from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from app.models.audit import SystemSetting


async def generate_operation_number(db: AsyncSession) -> str:
    """
    Generate a sequential operation number like RA-2026-0001.
    Uses the system_settings table with SELECT FOR UPDATE for thread-safety.
    """
    year = datetime.utcnow().year
    key = f"seq_operation_{year}"

    # Lock the row for this sequence key
    stmt = (
        select(SystemSetting)
        .where(SystemSetting.key == key)
        .with_for_update()
    )
    result = await db.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        # First operation of the year
        setting = SystemSetting(
            key=key,
            value={"current": 1},
            description=f"Operation sequence counter for {year}",
        )
        db.add(setting)
        current = 1
    else:
        current = setting.value.get("current", 0) + 1
        setting.value = {"current": current}

    await db.flush()
    return f"RA-{year}-{current:04d}"


async def generate_bdn_number(db: AsyncSession) -> str:
    """
    Generate a globally sequential BDN number like BDN-000001.
    Uses system_settings with SELECT FOR UPDATE.
    """
    key = "seq_bdn_global"

    stmt = (
        select(SystemSetting)
        .where(SystemSetting.key == key)
        .with_for_update()
    )
    result = await db.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = SystemSetting(
            key=key,
            value={"current": 1},
            description="Global BDN sequence counter",
        )
        db.add(setting)
        current = 1
    else:
        current = setting.value.get("current", 0) + 1
        setting.value = {"current": current}

    await db.flush()
    return f"BDN-{current:06d}"


async def generate_pfi_number(db: AsyncSession) -> str:
    """Generate a yearly sequential PFI number like PFI-2026-0001."""
    year = datetime.utcnow().year
    key = f"seq_pfi_{year}"

    stmt = select(SystemSetting).where(SystemSetting.key == key).with_for_update()
    result = await db.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = SystemSetting(
            key=key,
            value={"current": 1},
            description=f"PFI sequence counter for {year}",
        )
        db.add(setting)
        current = 1
    else:
        current = setting.value.get("current", 0) + 1
        setting.value = {"current": current}

    await db.flush()
    return f"PFI-{year}-{current:04d}"


async def generate_voucher_number(db: AsyncSession) -> str:
    """Generate a yearly sequential voucher number like VCH-2026-0001."""
    year = datetime.utcnow().year
    key = f"seq_voucher_{year}"

    stmt = select(SystemSetting).where(SystemSetting.key == key).with_for_update()
    result = await db.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = SystemSetting(
            key=key,
            value={"current": 1},
            description=f"Voucher sequence counter for {year}",
        )
        db.add(setting)
        current = 1
    else:
        current = setting.value.get("current", 0) + 1
        setting.value = {"current": current}

    await db.flush()
    return f"VCH-{year}-{current:04d}"


async def generate_invoice_number(db: AsyncSession) -> str:
    """Generate a yearly sequential invoice number like INV-2026-0001."""
    year = datetime.utcnow().year
    key = f"seq_invoice_{year}"

    stmt = select(SystemSetting).where(SystemSetting.key == key).with_for_update()
    result = await db.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = SystemSetting(
            key=key,
            value={"current": 1},
            description=f"Invoice sequence counter for {year}",
        )
        db.add(setting)
        current = 1
    else:
        current = setting.value.get("current", 0) + 1
        setting.value = {"current": current}

    await db.flush()
    return f"INV-{year}-{current:04d}"


async def generate_expense_voucher_number(db: AsyncSession) -> str:
    """Generate a yearly sequential expense voucher number like EXP-2026-0001."""
    year = datetime.utcnow().year
    key = f"seq_expense_{year}"

    stmt = select(SystemSetting).where(SystemSetting.key == key).with_for_update()
    result = await db.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = SystemSetting(
            key=key,
            value={"current": 1},
            description=f"Expense voucher sequence counter for {year}",
        )
        db.add(setting)
        current = 1
    else:
        current = setting.value.get("current", 0) + 1
        setting.value = {"current": current}

    await db.flush()
    return f"EXP-{year}-{current:04d}"
