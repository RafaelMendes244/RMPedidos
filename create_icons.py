from PIL import Image
import os

os.makedirs('static/images', exist_ok=True)

sizes = [72, 96, 128, 192, 384]

for size in sizes:
    img = Image.new('RGB', (size, size), color='#ea580c')
    img.save(f'static/images/icon-{size}x{size}.png')
    print(f'✓ icon-{size}x{size}.png criado')

# Badge
badge = Image.new('RGB', (72, 72), color='#ea580c')
badge.save('static/images/badge-72x72.png')
print('✓ badge-72x72.png criado')

print('\n✅ Todos os ícones PWA criados com sucesso!')
