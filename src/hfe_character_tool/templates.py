from __future__ import annotations

from types import MappingProxyType

from hfe_character_tool.hfworkshop_catalog import default_texture_selections, fallback_texture_parts
from hfe_character_tool.models import (
    AssetEntry,
    CharacterProject,
    InternalCharacter,
    InternalSkill,
    Template,
)

BUILTIN_TEMPLATES: tuple[Template, ...] = (
    Template(
        template_id="lucas-basic",
        name="Lucas 基础模板",
        version="1.0",
        required_assets=(),
        default_stats=MappingProxyType({"hp": 500, "mp": 500, "defense": 0}),
        skill_defaults=MappingProxyType(
            {
                "rising_slash": MappingProxyType(
                    {
                        "key": "D>A",
                        "mp_cost": 50,
                        "damage": 35,
                        "speed": 10,
                        "range": 80,
                    }
                )
            }
        ),
        editable_fields=(
            "character.id",
            "character.name",
            "character.description",
            "stats.hp",
            "stats.mp",
            "stats.defense",
            "skills.*.key",
            "skills.*.mp_cost",
            "skills.*.damage",
            "skills.*.speed",
            "skills.*.range",
            "item_frame_edits.*",
            "textures.*",
        ),
        mapping_note="基于 Lucas SPT/LMI 结构生成只读中间模型，不开放原始 SPT/LMI 编辑。",
    ),
)


def list_templates() -> tuple[Template, ...]:
    return BUILTIN_TEMPLATES


def get_template(template_id: str) -> Template:
    for template in BUILTIN_TEMPLATES:
        if template.template_id == template_id:
            return template
    raise KeyError(f"未知模板：{template_id}")


def initial_project(project_name: str, template_id: str, character_id: str) -> CharacterProject:
    template = get_template(template_id)
    return CharacterProject(
        project_name=project_name,
        template_id=template.template_id,
        template_version=template.version,
        source_role_id="lucas",
        character_id=character_id,
        character_name="Eyja" if character_id in {"eyja", "eyja0"} else project_name,
        character_name_zh="艾雅法拉" if character_id in {"eyja", "eyja0"} else "",
        description="",
        stats=MappingProxyType(dict(template.default_stats)),
        skills=MappingProxyType(
            {name: MappingProxyType(dict(value)) for name, value in template.skill_defaults.items()}
        ),
        item_frame_edits=(),
        texture_selections=MappingProxyType(default_texture_selections(fallback_texture_parts())),
    )


def check_template_version(project: CharacterProject) -> bool:
    return get_template(project.template_id).version == project.template_version


def to_internal_character(project: CharacterProject) -> InternalCharacter:
    template = get_template(project.template_id)
    skills = tuple(
        InternalSkill(
            skill_id=name,
            key=str(raw.get("key", "")),
            mp_cost=int(raw.get("mp_cost", 0)),
            damage=int(raw.get("damage", 0)),
            speed=int(raw.get("speed", 0)),
            range=int(raw.get("range", 0)),
        )
        for name, raw in project.skills.items()
    )
    assets = tuple(
        AssetEntry(file_name=asset.file_name, purpose=asset.purpose, status=asset.status)
        for asset in project.assets
    )
    return InternalCharacter(
        template=template,
        source_role_id=project.source_role_id or "lucas",
        character_id=project.character_id,
        character_name=project.character_name,
        character_name_zh=project.character_name_zh,
        description=project.description,
        stats=project.stats,
        skills=skills,
        item_frame_edits=project.item_frame_edits,
        assets=assets,
        texture_selections=project.texture_selections,
    )
