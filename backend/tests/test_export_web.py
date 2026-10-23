"""Pure helpers behind Export for web and the Camera tags."""
from fernkam.camera_tags import names
from fernkam.workflows.export_web import core_stem, folder_species, split_name


def test_camera_names():
    assert names("NIKON CORPORATION", "NIKON Z 9") == ("Nikon", "Z9")
    assert names("Canon", "Canon EOS 40D") == ("Canon", "40D")
    assert names("Canon", "Canon EOS 7D Mark II") == ("Canon", "7D Mark II")
    assert names("EASTMAN KODAK COMPANY", "KODAK EASYSHARE M1063 DIGITAL CAMERA") == ("Kodak", "Easyshare M1063")
    assert names("NIKON", "COOLPIX P900") == ("Nikon", "Coolpix P900")
    assert names("Google", "Pixel 4a") == ("Google", "Pixel 4a")
    assert names("VTech", "Kidizoom camera") == ("VTech", "Kidizoom")
    assert names(None, None) is None


def test_core_stem_finds_the_original():
    assert core_stem("edited_DSC6748.jpg") == "_DSC6748"
    assert core_stem("_5_I7993-DxO_DeepPRIME 3.jpg") == "_5_I7993"
    assert core_stem("IMG_1234 - Copy.jpg") == "IMG_1234"
    assert core_stem("_DSC0001.jpg") == "_DSC0001"


def test_species_from_names():
    assert folder_species("Wingtail Crayfish - Juvenile (Procambarus latipleurum)") == {
        "common": "Wingtail Crayfish", "note": "Juvenile", "latin": "Procambarus latipleurum"}
    assert folder_species("Box turtle (Terrapene sp.) [genus]") is None
    assert split_name("Osprey (Pandion haliaetus)") == ["Osprey", "Pandion haliaetus"]
    assert split_name("Crayfish") == ["Crayfish"]


def test_places_and_old_latin():
    from fernkam.workflows.export_web import old_latin, places
    names = ["Virginia Opossum (Didelphis virginiana)"]
    assert places("Virginia Opossum crossing a road, Marion Co., FL", "Archive/Level 2/Opossum/Virginia Opossum (Didelphis virginiana)", names) \
        == ["Florida", "Marion County"]
    assert places("", "Archive/Trips/Mexico 2007", []) == ["Mexico"]
    assert places("Seen in West Virginia", "", []) == ["West Virginia"]
    assert places("Texas Spiny Lizard basking", "", ["Texas Spiny Lizard (Sceloporus olivaceus)"]) == []
    assert places("Wakula Co. and Northern Liberty County; Swannee Co.", "", []) == ["Wakulla County", "Liberty County", "Suwannee County"]
    assert places("Cochise Co., Arizona", "", []) == ["Arizona", "Cochise County"]   # other states as written
    assert old_latin("Southern Toad (Bufo terrestris)", ["Southern Toad", "Anaxyrus terrestris"]) == ["Bufo terrestris"]
    assert old_latin("Southern Toad (Anaxyrus terrestris)", ["Anaxyrus terrestris"]) == []


def test_older_name_must_be_the_same_species():
    """His caption's Latin becomes a keyword only when GBIF says it is the photo's own species.
    The GBIF answers are pre-filled, so no network is needed."""
    import asyncio
    from fernkam.workflows.export_web import same_species
    taxa = {("match", "Bufo terrestris"): 1, 1: {"species": "Anaxyrus terrestris"},
            ("match", "Anaxyrus terrestris"): 2, 2: {"species": "Anaxyrus terrestris"},
            ("match", "Calocitta formosa"): 3, 3: {"species": "Calocitta formosa"},
            ("match", "Calocitta colliei"): 4, 4: {"species": "Calocitta colliei"},
            ("match", "Molossus nigricanis"): None}
    run = lambda name, first: asyncio.run(same_species(name, first, taxa))
    assert run("Bufo terrestris", (2, "Anaxyrus terrestris", "Southern Toad"))          # an older name: kept
    assert not run("Calocitta formosa", (4, "Calocitta colliei", "Black-throated Magpie-Jay"))   # misnamed: dropped
    assert not run("Molossus nigricanis", (5, "Molossus molossus", "Velvety Free-tailed Bat"))   # GBIF doesn't know it
    assert not run("Bufo terrestris", None)                                            # no species to compare with


def test_darwin_core_fields():
    from fernkam.workflows.export_web import _dwc
    taxa = {7: {"species": "Anaxyrus terrestris", "genus": "Anaxyrus", "family": "Bufonidae", "order": "Anura", "class": "Amphibia", "moved": True}}
    assert _dwc((7, "Bufo terrestris", "Southern Toad"), taxa, ["Florida", "USA", "Marion County"]) == {
        "TaxonScientificName": "Anaxyrus terrestris", "TaxonVernacularName": "Southern Toad", "TaxonGenus": "Anaxyrus",
        "TaxonFamily": "Bufonidae", "TaxonOrder": "Anura", "TaxonClass": "Amphibia",
        "DCCountry": "United States", "DCStateProvince": "Florida", "DCCounty": "Marion County"}
    assert _dwc(None, taxa, []) == {}


def test_species_folder():
    from fernkam.workflows.portfolio_keywords import species_folder
    w = "Lib/Crayfish/Wingtail Crayfish - Juvenile (Procambarus latipleurum)"
    assert species_folder(w + "/dng", "Lib") == w
    assert species_folder(w, "Lib") == w
    assert species_folder("Lib/Snakes/Tantilla (Tantilla)/Unidentified t pamlica", "Lib") == "Lib/Snakes/Tantilla (Tantilla)"
    assert species_folder("Lib/SORT/mixed", "Lib") is None
    assert species_folder("Top (Latin name)/Birds/Osprey", "Top (Latin name)/Birds") is None   # never above the folder picked
    assert species_folder(w + "/dng", w) == w                                                   # a species folder picked itself
