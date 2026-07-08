"""Pytest cases for the Quasi ad-name parser, written from real ad names
pulled from the Triple Whale export (2026-07). Run with:  pytest scripts/
"""
from parse_ad_name import parse_ad_name


def test_product_underscore_canonical():
    p = parse_ad_name(
        "GLOW_727_IT_H3_Graduation_DVW_AIPixarSongAnimation_Kian_Mark+Penny_ProblemAware"
    )
    assert p["convention"] == "product_underscore"
    assert p["batch_name"] == "GLOW"
    assert p["creative_no"] == "727"
    assert p["icp"] == "IT"
    assert p["hook"] == "H3"
    assert p["concept"] == "Graduation"
    assert p["format"] == "VID" and p["ad_type"] == "DVW"
    assert p["creator_type"] == "AIPixarSongAnimation"
    assert p["creator_name"] == "Kian"
    assert p["agency"] == "Mark+Penny"
    assert p["problem"] == "ProblemAware"


def test_product_underscore_hook_audience_swapped():
    # H# and audience appear in either order after the batch number.
    p = parse_ad_name(
        "GLOW_801_H1_IT_Exhusband-Church_DVW_AIPixarSongAnimation_Kian_Mark+Penny_ProblemAware"
    )
    assert p["hook"] == "H1"
    assert p["icp"] == "IT"
    assert p["concept"] == "Exhusband-Church"


def test_copy_suffix_dedup():
    base = "GLOW_727_IT_H3_Graduation_DVW_AIPixarSongAnimation_Kian_Mark+Penny_ProblemAware"
    for suffix in [" - Copy", " - Copy 2", " (1)", " - Copy (1)"]:
        p = parse_ad_name(base + suffix)
        assert p["dedup_key"] == base, suffix
        assert p["hook"] == "H3"


def test_trailing_version_number_stripped():
    p = parse_ad_name(
        "GLOW_728_H4_IT_ExHusbandRevenge_DVW_AiPixarSongAnimatuon_Kian_Penny_ProblemAware_1"
    )
    assert p["dedup_key"].endswith("ProblemAware")
    assert p["hook"] == "H4"
    # Misspelled style token is canonicalized.
    assert p["creator_type"] == "AIPixarSongAnimation"


def test_uppercase_na_tokens_skipped():
    p = parse_ad_name(
        "PDRN_495_H1_IT_DivorceTransformationAngle_DVW_AIANIMATION_MARTIN_NA_ANTONI_UNAWARE"
    )
    assert p["batch_name"] == "PDRN"
    assert p["problem"] == "Unaware"
    assert p["creator_type"] == "AIAnimation"
    assert p["creator_name"] == "MARTIN"
    assert p["agency"] == "ANTONI"
    assert p["concept"] == "DivorceTransformationAngle"


def test_product_dash_basic():
    p = parse_ad_name("COL-785-H1-NN-DVW-AI Animation-Martin_Daniyal")
    assert p["convention"] == "product_dash"
    assert p["batch_name"] == "COL"
    assert p["creative_no"] == "785"
    assert p["icp"] == "NN"
    assert p["format"] == "VID"
    assert p["creator_type"] == "AIAnimation"
    # Trailing underscore-joined pair splits into creator + strategist.
    assert p["creator_name"] == "Martin"
    assert p["agency"] == "Daniyal"


def test_product_dash_spaced_awareness():
    p = parse_ad_name("PDRN-498-H1-IT-AI Animation-Solution Aware-Martin_Daniyal")
    assert p["problem"] == "SolutionAware"
    # No explicit format token, but an animation style implies video.
    assert p["format"] == "VID"


def test_mhi_family():
    p = parse_ad_name(
        "MHI-H1-OBGCNV8Y-Marc-Sally-Video-Net_New-AI_VO_+_B_Roll-PDP-Product_Aware"
    )
    assert p["convention"] == "product_dash"
    assert p["batch_name"] == "MHI"
    assert p["hook"] == "H1"
    assert p["concept"] == "OBGCNV8Y"
    assert p["creator_name"] == "Marc"
    assert p["agency"] == "Sally"
    assert p["icp"] == "NN"          # Net_New normalizes to NN
    assert p["problem"] == "ProductAware"
    assert p["landing_page"] == "PDP"
    assert p["format"] == "VID"


def test_leading_number_family():
    p = parse_ad_name(
        "754_Glow_IT_H4_ExHusbandAndTherapist_DVW_AIPixarSongAnimation_Kian_Mark_ProblemAware"
    )
    assert p["convention"] == "leading_number"
    assert p["creative_no"] == "754"
    assert p["batch_name"] == "GLOW"
    assert p["hook"] == "H4"
    assert p["concept"] == "ExHusbandAndTherapist"
    assert p["creator_name"] == "Kian"
    assert p["agency"] == "Mark"


def test_pb_legacy_underscore():
    p = parse_ad_name("P4_B254_New_Video_ProductAware_MatureGlowSeeker_185S_Martin_Abdul_V2")
    assert p["convention"] == "pb_legacy"
    assert p["batch_name"] == "P4"
    assert p["creative_no"] == "254"
    assert p["problem"] == "ProductAware"
    assert p["format"] == "VID"


def test_pb_legacy_spaces_and_hook_word():
    p = parse_ad_name("P4 B13 with B828 Hook 1")
    assert p["convention"] == "pb_legacy"
    assert p["batch_name"] == "P4"
    assert p["creative_no"] == "13"
    assert p["hook"] == "H1"


def test_pb_legacy_bare_batch():
    p = parse_ad_name(
        "B664 Derma Truth-H1--NN-SKE-AI talking head-Jarrah-Ahmad-Solution aware"
    )
    assert p["convention"] == "pb_legacy"
    assert p["creative_no"] == "664"
    assert p["hook"] == "H1"
    assert p["icp"] == "NN"
    assert p["problem"] == "SolutionAware"


def test_collagen_folds_into_col():
    p = parse_ad_name(
        "Collagen_709_H3_NN_Pixar song-Soccer mom-Ex-husband_DVW_AI Pixar Song Animation__[Kian]_[Nabeel]_[problem Aware]"
    )
    assert p["batch_name"] == "COL"
    assert p["hook"] == "H3"
    assert p["icp"] == "NN"
    assert p["problem"] == "ProblemAware"
    assert p["creator_type"] == "AIPixarSongAnimation"
    assert p["creator_name"] == "Kian"
    assert p["agency"] == "Nabeel"


def test_awareness_typo_normalized():
    p = parse_ad_name("GLOW_430_IT_SKE_AI-Animation_Prodcut-Aware_KIAN_Abdul_H3")
    assert p["problem"] == "ProductAware"
    assert p["hook"] == "H3"


def test_freeform_returns_nulls():
    p = parse_ad_name("6")
    assert p["convention"] == "unknown"
    assert p["format"] is None
    assert p["hook"] is None


def test_empty_and_none_safe():
    assert parse_ad_name("")["convention"] == "unknown"
    assert parse_ad_name(None)["convention"] == "unknown"


def test_winner_fields_present_but_null():
    # No winner tags exist in Quasi names yet; schema keys must still exist
    # so the Win Rate tab and future tagging work unchanged.
    p = parse_ad_name("GLOW_727_IT_H3_Graduation_DVW_AIPixarSongAnimation_Kian_Mark+Penny_ProblemAware")
    for k in ("winner", "winner_year", "winner_month", "winner_week", "wtad", "paid_seed"):
        assert k in p and p[k] is None


def test_stable_schema_keys():
    keys = set(parse_ad_name("anything").keys())
    expected = {
        "ad_name", "convention", "format", "ad_type", "icp", "problem",
        "concept", "creative_no", "agency", "batch_name", "creator_type",
        "creator_name", "hook", "wtad", "landing_page", "date", "paid_seed",
        "winner", "winner_year", "winner_month", "winner_week", "dedup_key",
    }
    assert keys == expected


def test_b_batch_underscore_legacy():
    p = parse_ad_name("B806_New_Hook1_Video_66 Sec_Reda_Daniyal - Copy")
    assert p["convention"] == "pb_legacy"
    assert p["creative_no"] == "806"
    assert p["hook"] == "H1"
    assert p["format"] == "VID"
    assert p["paid_seed"] == "New"          # New/Fresh status token
    assert p["creator_name"] == "Reda"
    assert p["agency"] == "Daniyal"


def test_b_hash_batch_and_hook_hash():
    p = parse_ad_name(
        "B#828_New_Problem Aware_The Confidence Lost_Glass skin_Hook#3_UGC Head Talking_Model_Video_130Sec_Reda_Ali"
    )
    assert p["creative_no"] == "828"
    assert p["hook"] == "H3"
    assert p["problem"] == "ProblemAware"


def test_iter_mashup_family():
    p = parse_ad_name(
        "#47_ITER_TOFU_Elizabeth K Hooks_Winner iteration_ Mashups_Bio collagen mask_Iteration_Video_2Min25Sec_Reda_Ali_Hook1"
    )
    assert p["convention"] == "leading_number"
    assert p["creative_no"] == "47"
    assert p["hook"] == "H1"
    assert p["format"] == "VID"


def test_bracketed_family():
    p = parse_ad_name(
        "[56]_[New]_[Video]_[Unware]_[Mature Glow Seeker]_[Overnight Sleeping Mask]_[Text Video]_[Lucas Max]_[Henrique]_[Hook3]"
    )
    assert p["convention"] == "leading_number"
    assert p["creative_no"] == "56"
    assert p["problem"] == "Unaware"        # "Unware" typo normalized
    assert p["hook"] == "H3"


def test_spaced_dash_and_variation_suffix():
    p = parse_ad_name(
        "PDRN - 219 - H1 - IT - Comparison - MAT - AIvoiceover - Martin - Ivan - SolutionAware (Variation 1)"
    )
    assert p["convention"] == "product_dash"
    assert p["batch_name"] == "PDRN"
    assert p["creative_no"] == "219"
    assert p["icp"] == "IT"
    assert p["problem"] == "SolutionAware"
    assert not p["dedup_key"].endswith("(Variation 1)")


def test_adsplash_family():
    p = parse_ad_name("AdSplash_Concept 3 AI Ad-Bought Yesterday_H3 - Copy")
    assert p["convention"] == "adsplash"
    assert p["batch_name"] == "ADSPLASH"
    assert p["hook"] == "H3"


def test_id_hash_batch():
    p = parse_ad_name(
        "ID#1215_New_Video_MOF_Solution Aware_BFCM_Holiday Promo_UGC Head Talking_Jessica Peavey_Reda_Ali_Hook#2"
    )
    assert p["convention"] == "pb_legacy"
    assert p["creative_no"] == "1215"
    assert p["hook"] == "H2"
    assert p["problem"] == "SolutionAware"
