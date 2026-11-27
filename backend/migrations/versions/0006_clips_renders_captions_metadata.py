"""clips renders captions metadata

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "caption_templates",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("style", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_caption_templates_owner_id"), "caption_templates", ["owner_id"], unique=False)
    op.create_index(
        "uq_caption_templates_builtin_name",
        "caption_templates",
        ["name"],
        unique=True,
        postgresql_where=sa.text("is_builtin"),
    )
    op.create_table(
        "clips",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("current_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["highlight_candidates.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["video_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_clips_candidate_id"), "clips", ["candidate_id"], unique=False)
    op.create_index(op.f("ix_clips_created_by"), "clips", ["created_by"], unique=False)
    op.create_index(op.f("ix_clips_project_id"), "clips", ["project_id"], unique=False)
    op.create_index(op.f("ix_clips_source_id"), "clips", ["source_id"], unique=False)
    op.create_table(
        "clip_metadata",
        sa.Column("clip_id", sa.Uuid(), nullable=False),
        sa.Column("platform", sa.String(length=10), nullable=False),
        sa.Column("title", sa.String(length=200), server_default="", nullable=False),
        sa.Column("hook", sa.Text(), server_default="", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("short_description", sa.Text(), server_default="", nullable=False),
        sa.Column("hashtags", sa.ARRAY(sa.String(length=100)), server_default="{}", nullable=False),
        sa.Column("tags", sa.ARRAY(sa.String(length=100)), server_default="{}", nullable=False),
        sa.Column("thumbnail_text", sa.String(length=100), server_default="", nullable=False),
        sa.Column("category", sa.String(length=60), server_default="", nullable=False),
        sa.Column("audience", sa.String(length=200), server_default="", nullable=False),
        sa.Column("generated_by", sa.String(length=300), nullable=True),
        sa.Column("edited", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("platform in ('generic', 'youtube', 'instagram')", name="ck_clip_metadata_platform"),
        sa.ForeignKeyConstraint(["clip_id"], ["clips.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clip_id", "platform", name="uq_clip_metadata_clip_platform"),
    )
    op.create_index(op.f("ix_clip_metadata_clip_id"), "clip_metadata", ["clip_id"], unique=False)
    op.create_index(op.f("ix_clip_metadata_updated_by"), "clip_metadata", ["updated_by"], unique=False)
    op.create_table(
        "clip_versions",
        sa.Column("clip_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("aspect", sa.String(length=5), nullable=False),
        sa.Column("crop", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("background", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("caption_template_id", sa.Uuid(), nullable=True),
        sa.Column("caption_overrides", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("audio", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("transcript_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("aspect in ('9:16', '1:1', '16:9')", name="ck_clip_versions_aspect"),
        sa.CheckConstraint("end_ms > start_ms", name="ck_clip_versions_span"),
        sa.ForeignKeyConstraint(["caption_template_id"], ["caption_templates.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["clip_id"], ["clips.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clip_id", "version", name="uq_clip_versions_clip_version"),
    )
    op.create_index(
        op.f("ix_clip_versions_caption_template_id"), "clip_versions", ["caption_template_id"], unique=False
    )
    op.create_index(op.f("ix_clip_versions_clip_id"), "clip_versions", ["clip_id"], unique=False)
    op.create_index(op.f("ix_clip_versions_created_by"), "clip_versions", ["created_by"], unique=False)
    op.create_index(op.f("ix_clip_versions_transcript_id"), "clip_versions", ["transcript_id"], unique=False)
    op.create_table(
        "clip_renders",
        sa.Column("clip_version_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=8), server_default="pending", nullable=False),
        sa.Column("render_hash", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.Column("thumbnail_asset_id", sa.Uuid(), nullable=True),
        sa.Column("subtitle_asset_id", sa.Uuid(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("loudness_lufs", sa.Float(), nullable=True),
        sa.Column("qc_status", sa.String(length=4), nullable=True),
        sa.Column("qc_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind in ('preview', 'final')", name="ck_clip_renders_kind"),
        sa.CheckConstraint("qc_status in ('pass', 'warn', 'fail')", name="ck_clip_renders_qc_status"),
        sa.CheckConstraint("status in ('pending', 'done')", name="ck_clip_renders_status"),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["clip_version_id"], ["clip_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subtitle_asset_id"], ["media_assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["thumbnail_asset_id"], ["media_assets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_clip_renders_asset_id"), "clip_renders", ["asset_id"], unique=False)
    op.create_index(op.f("ix_clip_renders_clip_version_id"), "clip_renders", ["clip_version_id"], unique=False)
    op.create_index(op.f("ix_clip_renders_job_id"), "clip_renders", ["job_id"], unique=False)
    op.create_index(op.f("ix_clip_renders_requested_by"), "clip_renders", ["requested_by"], unique=False)
    op.create_index(op.f("ix_clip_renders_subtitle_asset_id"), "clip_renders", ["subtitle_asset_id"], unique=False)
    op.create_index(op.f("ix_clip_renders_thumbnail_asset_id"), "clip_renders", ["thumbnail_asset_id"], unique=False)
    op.create_index(
        "ix_clip_renders_version_kind_hash", "clip_renders", ["clip_version_id", "kind", "render_hash"], unique=False
    )
    # Autogenerate cannot see CHECK constraint changes: allow the new kinds of stored file.
    op.drop_constraint("ck_media_assets_kind", "media_assets", type_="check")
    op.create_check_constraint(
        "ck_media_assets_kind",
        "media_assets",
        "kind in ('original', 'proxy', 'waveform', 'thumbnail', 'audio', 'audio_prep', 'scenes', "
        "'clip_render', 'clip_preview', 'subtitle', 'clip_analysis')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_media_assets_kind", "media_assets", type_="check")
    op.execute("DELETE FROM media_assets WHERE kind IN ('clip_render', 'clip_preview', 'subtitle', 'clip_analysis')")
    op.create_check_constraint(
        "ck_media_assets_kind",
        "media_assets",
        "kind in ('original', 'proxy', 'waveform', 'thumbnail', 'audio', 'audio_prep', 'scenes')",
    )
    op.drop_index("ix_clip_renders_version_kind_hash", table_name="clip_renders")
    op.drop_index(op.f("ix_clip_renders_thumbnail_asset_id"), table_name="clip_renders")
    op.drop_index(op.f("ix_clip_renders_subtitle_asset_id"), table_name="clip_renders")
    op.drop_index(op.f("ix_clip_renders_requested_by"), table_name="clip_renders")
    op.drop_index(op.f("ix_clip_renders_job_id"), table_name="clip_renders")
    op.drop_index(op.f("ix_clip_renders_clip_version_id"), table_name="clip_renders")
    op.drop_index(op.f("ix_clip_renders_asset_id"), table_name="clip_renders")
    op.drop_table("clip_renders")
    op.drop_index(op.f("ix_clip_versions_transcript_id"), table_name="clip_versions")
    op.drop_index(op.f("ix_clip_versions_created_by"), table_name="clip_versions")
    op.drop_index(op.f("ix_clip_versions_clip_id"), table_name="clip_versions")
    op.drop_index(op.f("ix_clip_versions_caption_template_id"), table_name="clip_versions")
    op.drop_table("clip_versions")
    op.drop_index(op.f("ix_clip_metadata_updated_by"), table_name="clip_metadata")
    op.drop_index(op.f("ix_clip_metadata_clip_id"), table_name="clip_metadata")
    op.drop_table("clip_metadata")
    op.drop_index(op.f("ix_clips_source_id"), table_name="clips")
    op.drop_index(op.f("ix_clips_project_id"), table_name="clips")
    op.drop_index(op.f("ix_clips_created_by"), table_name="clips")
    op.drop_index(op.f("ix_clips_candidate_id"), table_name="clips")
    op.drop_table("clips")
    op.drop_index(
        "uq_caption_templates_builtin_name", table_name="caption_templates", postgresql_where=sa.text("is_builtin")
    )
    op.drop_index(op.f("ix_caption_templates_owner_id"), table_name="caption_templates")
    op.drop_table("caption_templates")
