"""Initial schema — all 19 tables with ENUMs

Revision ID: 001
Revises:
Create Date: 2026-03-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── ENUM definitions ──────────────────────────────────────────────────────────

user_role_enum = postgresql.ENUM(
    "bunker_manager", "ops_supervisor", "logistics_officer",
    "marine_manager", "finance_manager", "client",
    name="user_role",
)

operation_type_enum = postgresql.ENUM(
    "truck_only", "vessel_only", "full_operation",
    name="operation_type",
)

operation_status_enum = postgresql.ENUM(
    "draft", "tasks_assigned", "awaiting_feedback", "feedback_submitted",
    "feedback_approved", "feedback_rejected", "pfi_linked", "payment_processing",
    "payment_confirmed", "vessel_operations", "bdn_pending", "bdn_approved",
    "invoiced", "completed", "archived", "cancelled",
    name="operation_status",
)

task_type_enum = postgresql.ENUM(
    "truck_logistics", "vessel_operations", "marine_discharge", "finance_processing",
    name="task_type",
)

task_status_enum = postgresql.ENUM(
    "pending", "in_progress", "completed", "cancelled",
    name="task_status",
)

priority_enum = postgresql.ENUM(
    "low", "normal", "high", "urgent",
    name="priority",
)

truck_status_enum = postgresql.ENUM(
    "available", "assigned", "in_transit", "discharging", "maintenance", "out_of_service",
    name="truck_status",
)

truck_op_status_enum = postgresql.ENUM(
    "pending", "loading", "in_transit", "arrived", "discharging", "completed", "cancelled",
    name="truck_op_status",
)

vessel_status_enum = postgresql.ENUM(
    "available", "assigned", "operating", "maintenance",
    name="vessel_status",
)

rob_entry_type_enum = postgresql.ENUM(
    "initial", "discharge", "replenishment", "adjustment", "correction",
    name="rob_entry_type",
)

bdn_status_enum = postgresql.ENUM(
    "pending", "approved", "rejected",
    name="bdn_status",
)

pfi_status_enum = postgresql.ENUM(
    "pending", "payment_initiated", "paid",
    name="pfi_status",
)

invoice_status_enum = postgresql.ENUM(
    "draft", "sent", "paid", "overdue", "cancelled",
    name="invoice_status",
)

feedback_status_enum = postgresql.ENUM(
    "pending", "approved", "rejected", "resubmitted",
    name="feedback_status",
)

doc_type_enum = postgresql.ENUM(
    "bdn", "invoice", "payment_voucher", "pfi", "report", "clearance", "other",
    name="doc_type",
)

notification_type_enum = postgresql.ENUM(
    "task_assigned", "approval_needed", "approved", "rejected",
    "payment_update", "rob_alert", "bdn_ready", "milestone", "system",
    name="notification_type",
)

ALL_ENUMS = [
    user_role_enum, operation_type_enum, operation_status_enum,
    task_type_enum, task_status_enum, priority_enum,
    truck_status_enum, truck_op_status_enum, vessel_status_enum,
    rob_entry_type_enum, bdn_status_enum, pfi_status_enum,
    invoice_status_enum, feedback_status_enum, doc_type_enum,
    notification_type_enum,
]


def upgrade() -> None:
    conn = op.get_bind()

    # ── Create all ENUMs ──────────────────────────────────────────────────────
    for enum in ALL_ENUMS:
        enum.create(conn, checkfirst=True)

    # ── 1. users ──────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("auth_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(150), nullable=False),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("role", postgresql.ENUM(name="user_role", create_type=False), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("avatar_url", sa.Text, nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("auth_id", name="uq_users_auth_id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    # ── 2. system_settings ────────────────────────────────────────────────────
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", postgresql.JSONB, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"],
                                name="fk_system_settings_updated_by_users"),
    )

    # ── 3. vessels ────────────────────────────────────────────────────────────
    op.create_table(
        "vessels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("vessel_name", sa.String(200), nullable=False),
        sa.Column("imo_number", sa.String(20), nullable=True),
        sa.Column("vessel_type", sa.String(100), nullable=True),
        sa.Column("flag_state", sa.String(100), nullable=True),
        sa.Column("capacity_mt", sa.Numeric(12, 3), nullable=True),
        sa.Column("current_rob_mt", sa.Numeric(12, 3), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("rob_threshold_mt", sa.Numeric(12, 3), nullable=False,
                  server_default=sa.text("100")),
        sa.Column("current_location", sa.Text, nullable=True),
        sa.Column("status", postgresql.ENUM(name="vessel_status", create_type=False),
                  nullable=False, server_default="available"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("imo_number", name="uq_vessels_imo_number"),
    )

    # ── 4. operations ─────────────────────────────────────────────────────────
    op.create_table(
        "operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("operation_number", sa.String(20), nullable=False),
        sa.Column("type", postgresql.ENUM(name="operation_type", create_type=False), nullable=False),
        sa.Column("status", postgresql.ENUM(name="operation_status", create_type=False),
                  nullable=False, server_default="draft"),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expected_volume_mt", sa.Numeric(12, 3), nullable=True),
        sa.Column("actual_volume_mt", sa.Numeric(12, 3), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_reason", sa.Text, nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="NGN"),
        sa.Column("vessel_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("operation_number", name="uq_operations_operation_number"),
        sa.ForeignKeyConstraint(["client_id"], ["users.id"],
                                name="fk_operations_client_id_users"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"],
                                name="fk_operations_created_by_users"),
        sa.ForeignKeyConstraint(["vessel_id"], ["vessels.id"],
                                name="fk_operations_vessel_id_vessels"),
    )
    op.create_index("ix_operations_status", "operations", ["status"])
    op.create_index("ix_operations_type", "operations", ["type"])
    op.create_index("ix_operations_client_id", "operations", ["client_id"])
    op.create_index("ix_operations_created_at", "operations", ["created_at"])
    op.create_index("ix_operations_deleted_at", "operations", ["deleted_at"])

    # ── 5. operation_status_history ───────────────────────────────────────────
    op.create_table(
        "operation_status_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status",
                  postgresql.ENUM(name="operation_status", create_type=False), nullable=True),
        sa.Column("to_status",
                  postgresql.ENUM(name="operation_status", create_type=False), nullable=False),
        sa.Column("changed_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"],
                                name="fk_op_status_history_operation_id_operations"),
        sa.ForeignKeyConstraint(["changed_by"], ["users.id"],
                                name="fk_op_status_history_changed_by_users"),
    )
    op.create_index("ix_operation_status_history_operation_id",
                    "operation_status_history", ["operation_id"])

    # ── 6. task_assignments ───────────────────────────────────────────────────
    op.create_table(
        "task_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_to", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_type", postgresql.ENUM(name="task_type", create_type=False), nullable=False),
        sa.Column("status", postgresql.ENUM(name="task_status", create_type=False),
                  nullable=False, server_default="pending"),
        sa.Column("priority", postgresql.ENUM(name="priority", create_type=False),
                  nullable=False, server_default="normal"),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("instructions", sa.Text, nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"],
                                name="fk_task_assignments_operation_id_operations"),
        sa.ForeignKeyConstraint(["assigned_to"], ["users.id"],
                                name="fk_task_assignments_assigned_to_users"),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"],
                                name="fk_task_assignments_assigned_by_users"),
    )
    op.create_index("ix_task_assignments_operation_id", "task_assignments", ["operation_id"])
    op.create_index("ix_task_assignments_assigned_to", "task_assignments", ["assigned_to"])

    # ── 7. trucks ─────────────────────────────────────────────────────────────
    op.create_table(
        "trucks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("truck_number", sa.String(50), nullable=False),
        sa.Column("capacity_mt", sa.Numeric(10, 3), nullable=False),
        sa.Column("driver_name", sa.String(150), nullable=True),
        sa.Column("driver_phone", sa.String(20), nullable=True),
        sa.Column("status", postgresql.ENUM(name="truck_status", create_type=False),
                  nullable=False, server_default="available"),
        sa.Column("current_location", sa.Text, nullable=True),
        sa.Column("gps_lat", sa.Numeric(10, 7), nullable=True),
        sa.Column("gps_lng", sa.Numeric(10, 7), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("truck_number", name="uq_trucks_truck_number"),
    )

    # ── 8. truck_operations ───────────────────────────────────────────────────
    op.create_table(
        "truck_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("truck_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("logged_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity_loaded_mt", sa.Numeric(12, 3), nullable=True),
        sa.Column("quantity_discharged_mt", sa.Numeric(12, 3), nullable=True),
        sa.Column("variance_mt", sa.Numeric(12, 3), nullable=True),
        sa.Column("loading_location", sa.Text, nullable=True),
        sa.Column("discharge_location", sa.Text, nullable=True),
        sa.Column("destination_vessel_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("transit_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transit_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discharge_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discharge_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", postgresql.ENUM(name="truck_op_status", create_type=False),
                  nullable=False, server_default="pending"),
        sa.Column("gps_start_lat", sa.Numeric(10, 7), nullable=True),
        sa.Column("gps_start_lng", sa.Numeric(10, 7), nullable=True),
        sa.Column("gps_end_lat", sa.Numeric(10, 7), nullable=True),
        sa.Column("gps_end_lng", sa.Numeric(10, 7), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"],
                                name="fk_truck_operations_operation_id_operations"),
        sa.ForeignKeyConstraint(["truck_id"], ["trucks.id"],
                                name="fk_truck_operations_truck_id_trucks"),
        sa.ForeignKeyConstraint(["logged_by"], ["users.id"],
                                name="fk_truck_operations_logged_by_users"),
        sa.ForeignKeyConstraint(["destination_vessel_id"], ["vessels.id"],
                                name="fk_truck_operations_destination_vessel_id_vessels"),
    )
    op.create_index("ix_truck_operations_operation_id", "truck_operations", ["operation_id"])
    op.create_index("ix_truck_operations_truck_id", "truck_operations", ["truck_id"])

    # ── 9. truck_feedback ─────────────────────────────────────────────────────
    op.create_table(
        "truck_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submitted_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("truck_ids", postgresql.JSONB, nullable=False),
        sa.Column("status", postgresql.ENUM(name="feedback_status", create_type=False),
                  nullable=False, server_default="pending"),
        sa.Column("readiness_summary", sa.Text, nullable=False),
        sa.Column("truck_details", postgresql.JSONB, nullable=False),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"],
                                name="fk_truck_feedback_operation_id_operations"),
        sa.ForeignKeyConstraint(["submitted_by"], ["users.id"],
                                name="fk_truck_feedback_submitted_by_users"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"],
                                name="fk_truck_feedback_reviewed_by_users"),
    )
    op.create_index("ix_truck_feedback_operation_id", "truck_feedback", ["operation_id"])

    # ── 10. rob_entries ───────────────────────────────────────────────────────
    op.create_table(
        "rob_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("vessel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("entry_type",
                  postgresql.ENUM(name="rob_entry_type", create_type=False), nullable=False),
        sa.Column("quantity_mt", sa.Numeric(12, 3), nullable=False),
        sa.Column("rob_before_mt", sa.Numeric(12, 3), nullable=False),
        sa.Column("rob_after_mt", sa.Numeric(12, 3), nullable=False),
        sa.Column("recorded_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_description", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["vessel_id"], ["vessels.id"],
                                name="fk_rob_entries_vessel_id_vessels"),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"],
                                name="fk_rob_entries_operation_id_operations"),
        sa.ForeignKeyConstraint(["recorded_by"], ["users.id"],
                                name="fk_rob_entries_recorded_by_users"),
    )
    op.create_index("ix_rob_entries_vessel_id", "rob_entries", ["vessel_id"])
    op.create_index("ix_rob_entries_operation_id", "rob_entries", ["operation_id"])

    # ── 11. bdns ──────────────────────────────────────────────────────────────
    op.create_table(
        "bdns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("bdn_number", sa.String(20), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vessel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generated_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", postgresql.ENUM(name="bdn_status", create_type=False),
                  nullable=False, server_default="pending"),
        sa.Column("quantity_delivered_mt", sa.Numeric(12, 3), nullable=False),
        sa.Column("product_type", sa.String(100), nullable=True),
        sa.Column("density", sa.Numeric(8, 4), nullable=True),
        sa.Column("temperature", sa.Numeric(6, 2), nullable=True),
        sa.Column("delivery_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pdf_url", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("bdn_number", name="uq_bdns_bdn_number"),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"],
                                name="fk_bdns_operation_id_operations"),
        sa.ForeignKeyConstraint(["vessel_id"], ["vessels.id"],
                                name="fk_bdns_vessel_id_vessels"),
        sa.ForeignKeyConstraint(["generated_by"], ["users.id"],
                                name="fk_bdns_generated_by_users"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"],
                                name="fk_bdns_reviewed_by_users"),
    )
    op.create_index("ix_bdns_operation_id", "bdns", ["operation_id"])

    # ── 12. pfis ──────────────────────────────────────────────────────────────
    op.create_table(
        "pfis",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("pfi_number", sa.String(50), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("linked_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="NGN"),
        sa.Column("exchange_rate", sa.Numeric(12, 6), nullable=True),
        sa.Column("amount_ngn", sa.Numeric(15, 2), nullable=True),
        sa.Column("supplier_name", sa.String(200), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("document_url", sa.Text, nullable=True),
        sa.Column("status", postgresql.ENUM(name="pfi_status", create_type=False),
                  nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("pfi_number", name="uq_pfis_pfi_number"),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"],
                                name="fk_pfis_operation_id_operations"),
        sa.ForeignKeyConstraint(["linked_by"], ["users.id"],
                                name="fk_pfis_linked_by_users"),
    )
    op.create_index("ix_pfis_operation_id", "pfis", ["operation_id"])

    # ── 13. payments ──────────────────────────────────────────────────────────
    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("pfi_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("processed_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("payment_method", sa.String(50), nullable=True),
        sa.Column("payment_reference", sa.String(200), nullable=True),
        sa.Column("payment_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("voucher_number", sa.String(50), nullable=False),
        sa.Column("voucher_url", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("voucher_number", name="uq_payments_voucher_number"),
        sa.ForeignKeyConstraint(["pfi_id"], ["pfis.id"],
                                name="fk_payments_pfi_id_pfis"),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"],
                                name="fk_payments_operation_id_operations"),
        sa.ForeignKeyConstraint(["processed_by"], ["users.id"],
                                name="fk_payments_processed_by_users"),
    )
    op.create_index("ix_payments_operation_id", "payments", ["operation_id"])

    # ── 14. invoices ──────────────────────────────────────────────────────────
    op.create_table(
        "invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("invoice_number", sa.String(50), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bdn_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generated_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("exchange_rate", sa.Numeric(12, 6), nullable=True),
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("total_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("due_date", sa.Date, nullable=True),
        sa.Column("status", postgresql.ENUM(name="invoice_status", create_type=False),
                  nullable=False, server_default="draft"),
        sa.Column("pdf_url", sa.Text, nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("invoice_number", name="uq_invoices_invoice_number"),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"],
                                name="fk_invoices_operation_id_operations"),
        sa.ForeignKeyConstraint(["bdn_id"], ["bdns.id"],
                                name="fk_invoices_bdn_id_bdns"),
        sa.ForeignKeyConstraint(["client_id"], ["users.id"],
                                name="fk_invoices_client_id_users"),
        sa.ForeignKeyConstraint(["generated_by"], ["users.id"],
                                name="fk_invoices_generated_by_users"),
    )
    op.create_index("ix_invoices_operation_id", "invoices", ["operation_id"])
    op.create_index("ix_invoices_client_id", "invoices", ["client_id"])

    # ── 15. documents ─────────────────────────────────────────────────────────
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_type", postgresql.ENUM(name="doc_type", create_type=False), nullable=False),
        sa.Column("file_name", sa.String(500), nullable=False),
        sa.Column("file_url", sa.Text, nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger, nullable=True),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"],
                                name="fk_documents_operation_id_operations"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"],
                                name="fk_documents_uploaded_by_users"),
    )
    op.create_index("ix_documents_operation_id", "documents", ["operation_id"])

    # ── 16. notifications ─────────────────────────────────────────────────────
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("type",
                  postgresql.ENUM(name="notification_type", create_type=False), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("priority", postgresql.ENUM(name="priority", create_type=False),
                  nullable=False, server_default="normal"),
        sa.Column("is_read", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action_url", sa.Text, nullable=True),
        sa.Column("delivery_channels",
                  postgresql.ARRAY(sa.String), nullable=False,
                  server_default=sa.text("'{in_app}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                name="fk_notifications_user_id_users"),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"],
                                name="fk_notifications_operation_id_operations"),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_is_read", "notifications", ["is_read"])

    # ── 17. audit_logs ────────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("changes", postgresql.JSONB, nullable=True),
        sa.Column("ip_address", sa.Text, nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                name="fk_audit_logs_user_id_users"),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"],
                                name="fk_audit_logs_operation_id_operations"),
    )
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_operation_id", "audit_logs", ["operation_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    # ── 18. delegation_assignments ────────────────────────────────────────────
    op.create_table(
        "delegation_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("delegator_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("delegate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_scope", postgresql.ARRAY(sa.String), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_auto_escalation", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["delegator_id"], ["users.id"],
                                name="fk_delegation_assignments_delegator_id_users"),
        sa.ForeignKeyConstraint(["delegate_id"], ["users.id"],
                                name="fk_delegation_assignments_delegate_id_users"),
    )

    # ── 19. client_milestones ─────────────────────────────────────────────────
    op.create_table(
        "client_milestones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("milestone_type", sa.String(100), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("reached_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("is_visible", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"],
                                name="fk_client_milestones_operation_id_operations"),
    )
    op.create_index("ix_client_milestones_operation_id",
                    "client_milestones", ["operation_id"])

    # ── Seed default system_settings ─────────────────────────────────────────
    op.execute(
        sa.text("""
        INSERT INTO system_settings (key, value, description) VALUES
        ('auto_escalation_timeout_hours', '4'::jsonb,
         'Hours before approval auto-escalation'),
        ('rob_default_threshold_mt', '100'::jsonb,
         'Default ROB alert threshold in metric tonnes'),
        ('max_upload_size_mb', '10'::jsonb,
         'Maximum file upload size in MB')
        ON CONFLICT (key) DO NOTHING;
        """)
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Drop tables in reverse dependency order
    tables_in_order = [
        "client_milestones",
        "delegation_assignments",
        "audit_logs",
        "notifications",
        "documents",
        "invoices",
        "payments",
        "pfis",
        "bdns",
        "rob_entries",
        "truck_feedback",
        "truck_operations",
        "trucks",
        "task_assignments",
        "operation_status_history",
        "operations",
        "vessels",
        "system_settings",
        "users",
    ]
    for table in tables_in_order:
        op.drop_table(table)

    # Drop all ENUMs
    for enum in reversed(ALL_ENUMS):
        enum.drop(conn, checkfirst=True)
