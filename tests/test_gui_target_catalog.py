from __future__ import annotations

from hfe_character_tool.gui import (
    NewProjectTemplateChoice,
    select_template_label_after_refresh,
    target_template_choices,
)
from hfe_character_tool.hfworkshop_catalog import CharacterTemplateCatalogEntry
from hfe_character_tool.templates import list_templates


def test_new_project_template_choices_include_all_available_target_roles() -> None:
    choices = target_template_choices(
        (
            CharacterTemplateCatalogEntry(
                template_id="target:lucas",
                role_id="lucas",
                label="Lucas (lucas)",
                spt_class="Data.Global_lucasSpt",
                lmi_class="Data.Global_lucasLmi",
            ),
            CharacterTemplateCatalogEntry(
                template_id="target:raye",
                role_id="raye",
                label="Raye (raye)",
                spt_class="Data.Global_rayeSpt",
                lmi_class="Data.Global_rayeLmi",
            ),
            CharacterTemplateCatalogEntry(
                template_id="target:heater",
                role_id="heater",
                label="Heater (heater)",
                spt_class="Data.Global_heaterSpt",
                lmi_class="",
                available=False,
                unavailable_reason="missing LMI",
            ),
        ),
        list_templates(),
    )

    assert len(choices) == 2
    assert choices[0].template_id == "lucas-basic"
    assert choices[0].role_id == "lucas"
    assert choices[0].label == "Lucas (lucas)"
    assert choices[1].template_id == "lucas-basic"
    assert choices[1].role_id == "raye"
    assert choices[1].label == "Raye (raye)"


def test_new_project_template_choices_hide_lucas_when_target_lacks_lmi() -> None:
    choices = target_template_choices(
        (
            CharacterTemplateCatalogEntry(
                template_id="target:lucas",
                role_id="lucas",
                label="Lucas (lucas)",
                spt_class="Data.Global_lucasSpt",
                lmi_class="",
                available=False,
                unavailable_reason="missing LMI",
            ),
        ),
        list_templates(),
    )

    assert choices == ()


def test_refresh_preserves_selected_template_by_role_id_when_label_changes() -> None:
    old_choices = (
        NewProjectTemplateChoice(
            label="z_woman01 (z_woman01)",
            template_id="lucas-basic",
            role_id="z_woman01",
        ),
        NewProjectTemplateChoice(
            label="Lucas (lucas)",
            template_id="lucas-basic",
            role_id="lucas",
        ),
    )
    new_choices = (
        NewProjectTemplateChoice(
            label="Z Woman 01 (z_woman01)",
            template_id="lucas-basic",
            role_id="z_woman01",
        ),
        NewProjectTemplateChoice(
            label="Lucas (lucas)",
            template_id="lucas-basic",
            role_id="lucas",
        ),
    )

    selected = select_template_label_after_refresh(
        current_label="z_woman01 (z_woman01)",
        old_choices=old_choices,
        new_choices=new_choices,
    )

    assert selected == "Z Woman 01 (z_woman01)"
