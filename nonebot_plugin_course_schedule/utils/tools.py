import base64
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageOps

def image_to_base64(img: Image.Image, format='JPEG',quality=75) -> str:
    if img.mode == 'RGBA':
        img = img.convert('RGB')
    output_buffer = BytesIO()
    img.save(output_buffer, format=format, quality=quality)
    byte_data = output_buffer.getvalue()
    base64_str = base64.b64encode(byte_data).decode()
    return 'base64://' + base64_str