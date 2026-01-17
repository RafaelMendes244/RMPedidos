"""
Script para gerar ícones PNG para PWA a partir do SVG
Execute: python generate_pwa_icons.py
"""
import os
import subprocess

STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
SVG_FILE = os.path.join(STATIC_DIR, 'pwa-icons.svg')
IMAGES_DIR = os.path.join(STATIC_DIR, 'images')

# Criar diretório se não existir
os.makedirs(IMAGES_DIR, exist_ok=True)

# Tamanhos necessários para PWA
sizes = [72, 96, 128, 192, 384, 512]

print("Gerando ícones PWA...")

# Se houver ImageMagick instalado, usá-lo para converter
try:
    for size in sizes:
        output = os.path.join(IMAGES_DIR, f'icon-{size}x{size}.png')
        cmd = [
            'convert',
            '-background', 'none',
            '-resize', f'{size}x{size}',
            f'{SVG_FILE}',
            output
        ]
        subprocess.run(cmd, check=True)
        print(f"✓ Criado: icon-{size}x{size}.png")
except FileNotFoundError:
    print("ImageMagick não instalado. Usando alternativa...")
    
    # Alternativa com cairosvg se disponível
    try:
        from cairosvg import svg2png
        
        for size in sizes:
            output = os.path.join(IMAGES_DIR, f'icon-{size}x{size}.png')
            svg2png(url=SVG_FILE, write_to=output, output_width=size, output_height=size)
            print(f"✓ Criado: icon-{size}x{size}.png")
    except ImportError:
        print("⚠️  Nenhuma ferramenta de conversão SVG disponível.")
        print("   Instale: pip install cairosvg")
        print("   Ou instale ImageMagick no seu sistema.")

# Criar badge menor
badge_size = 72
output = os.path.join(IMAGES_DIR, f'badge-{badge_size}x{badge_size}.png')
try:
    subprocess.run([
        'convert',
        '-background', '#ea580c',
        '-resize', f'{badge_size}x{badge_size}',
        f'{SVG_FILE}',
        output
    ], check=True)
    print(f"✓ Criado: badge-{badge_size}x{badge_size}.png")
except FileNotFoundError:
    print(f"⚠️  Não foi possível criar badge")

print("\n✅ Ícones PWA gerados com sucesso!")
print(f"   Localização: {IMAGES_DIR}")
