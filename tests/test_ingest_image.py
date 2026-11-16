"""The image adapter: a photo or scan becomes a one-page PDF the existing pipeline can read."""

import fitz
from PIL import Image

from docintel.ingest.image import image_to_pdf
from docintel.pipeline import classify_pdf


def make_image(path, size=(1500, 2000), color=230, **save_options):
    Image.new("L", size, color).save(path, **save_options)
    return path


def test_a_photo_becomes_one_scan_page_sized_from_its_pixels_at_150_dpi(tmp_path):
    pdf = image_to_pdf(make_image(tmp_path / "scan.png"), tmp_path / "scan.pdf")
    with fitz.open(pdf) as doc:
        assert doc.page_count == 1
        assert (doc[0].rect.width, doc[0].rect.height) == (720.0, 960.0)  # 1500x2000 px * 72/150
    assert classify_pdf(pdf)["scan_pages"] == [1]


def test_a_phones_exif_rotation_is_applied_so_the_page_is_upright(tmp_path):
    # Stored landscape (2000x1500) with a dark square at its top-left, flagged "rotate 90 CW".
    # Shown upright it is portrait, and by the EXIF spec that corner lands at the top-right.
    raw = Image.new("L", (2000, 1500), 230)
    raw.paste(0, (0, 0, 300, 300))
    exif = Image.Exif()
    exif[0x0112] = 6
    raw.save(tmp_path / "photo.jpg", exif=exif)

    pdf = image_to_pdf(tmp_path / "photo.jpg", tmp_path / "photo.pdf")
    with fitz.open(pdf) as doc:
        page = doc[0]
        assert (page.rect.width, page.rect.height) == (720.0, 960.0)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(0.25, 0.25))
        assert pixmap.pixel(pixmap.width - 10, 10)[0] < 80  # top-right is the dark square
        assert pixmap.pixel(10, 10)[0] > 180  # top-left is paper


def test_each_frame_of_a_multipage_tiff_becomes_a_page(tmp_path):
    frames = [Image.new("L", (1500, 2000), 230), Image.new("L", (1500, 1500), 230), Image.new("L", (750, 1000), 230)]
    frames[0].save(tmp_path / "scan.tif", save_all=True, append_images=frames[1:])
    pdf = image_to_pdf(tmp_path / "scan.tif", tmp_path / "scan.pdf")
    with fitz.open(pdf) as doc:
        assert [(page.rect.width, page.rect.height) for page in doc] == [(720, 960), (720, 720), (360, 480)]


def desk_photo(path):
    """A 700x1000 sheet with text-like lines, photographed at an angle on a dark desk."""
    import cv2
    import numpy as np

    sheet = np.full((1000, 700), 235, np.uint8)
    for y in range(120, 900, 60):
        cv2.line(sheet, (80, y), (620, y), 90, 6)
    corners = np.float32([[0, 0], [700, 0], [700, 1000], [0, 1000]])
    in_photo = np.float32([[150, 200], [1000, 150], [1050, 1450], [100, 1350]])
    matrix = cv2.getPerspectiveTransform(corners, in_photo)
    photo = np.full((1600, 1200), 40, np.uint8)
    warped = cv2.warpPerspective(sheet, matrix, (1200, 1600), borderValue=0)
    mask = cv2.warpPerspective(np.full_like(sheet, 255), matrix, (1200, 1600))
    photo[mask > 0] = warped[mask > 0]
    Image.fromarray(photo).save(path)
    return path


def test_a_page_photographed_on_a_desk_is_straightened_and_cropped_to_the_paper(tmp_path):
    pdf = image_to_pdf(desk_photo(tmp_path / "desk.png"), tmp_path / "desk.pdf")
    with fitz.open(pdf) as doc:
        page = doc[0]
        assert 0.6 < page.rect.width / page.rect.height < 0.8  # the sheet is 0.7, the photo is 0.75
        assert page.rect.width < 1200 * 72 / 150  # the desk is cropped away
        pixmap = page.get_pixmap(matrix=fitz.Matrix(0.25, 0.25))
        corners = [(2, 2), (pixmap.width - 3, 2), (2, pixmap.height - 3), (pixmap.width - 3, pixmap.height - 3)]
        assert all(pixmap.pixel(x, y)[0] > 180 for x, y in corners)  # paper, not desk


def test_a_flat_scan_that_fills_the_frame_is_left_alone(tmp_path):
    pdf = image_to_pdf(make_image(tmp_path / "flat.png", size=(1500, 2000)), tmp_path / "flat.pdf")
    with fitz.open(pdf) as doc:
        assert (doc[0].rect.width, doc[0].rect.height) == (720.0, 960.0)
