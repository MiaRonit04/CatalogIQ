"""Generate 240 realistic listings: 200 unique contents + 40 normalized duplicates."""
import csv
from pathlib import Path

BASE = [
 ('AMUL butter', 'Salted dairy butter, keep refrigerated', ['100G','200G','500G','1KG','250G']),
 ('Tata salt', 'Iodised cooking salt', ['100G','200G','500G','1KG','2KG']),
 ('Fortune rice', 'Basmati rice for everyday meals', ['500G','1KG','2KG','5KG','10KG']),
 ('Aashirvaad atta', 'Whole wheat flour', ['500G','1KG','2KG','5KG','10KG']),
 ('Britannia biscuit', 'Crunchy tea-time snack', ['50G','100G','200G','400G','600G']),
 ('Nestle milk', 'UHT toned milk carton', ['200ML','500ML','1L','2L','100ML']),
 ('pasta penne', 'Durum wheat pasta', ['100G','200G','500G','1KG','250G']),
 ('rolled oats', 'Plain breakfast cereal', ['100G','200G','500G','1KG','750G']),
 ('Tata tea', 'Loose leaf black tea', ['100G','200G','250G','500G','1KG']),
 ('Nescafe coffee', 'Instant coffee in glass jar', ['25G','50G','100G','200G','250G']),
 ('orange juice', 'Fruit beverage, no added sugar', ['200ML','500ML','1L','2L','250ML']),
 ('mineral water', 'Packaged drinking water', ['250ML','500ML','1L','2L','5L']),
 ('cola drink', 'Carbonated soft drink', ['200ML','300ML','500ML','1L','2L']),
 ('Dove shampoo', 'Daily moisture hair wash', ['80ML','180ML','250ML','500ML','1L']),
 ('Colgate toothpaste', 'Fluoride toothpaste', ['50G','100G','150G','200G','300G']),
 ('Dove soap', 'Gentle cleansing bar', ['50G','75G','100G','125G','150G']),
 ('body lotion', 'Unscented daily moisturizer', ['50ML','100ML','200ML','400ML','500ML']),
 ('roll on deodorant', 'Fresh fragrance', ['25ML','40ML','50ML','75ML','100ML']),
 ('Surf Excel detergent', 'Front load washing powder', ['500G','1KG','2KG','4KG','5KG']),
 ('Vim dishwash', 'Lemon dish cleaning gel', ['100ML','250ML','500ML','1L','2L']),
 ('floor cleaner', 'Citrus fragrance', ['250ML','500ML','1L','2L','5L']),
 ('facial tissue', 'Two ply soft tissues', ['50 sheets','100 sheets','150 sheets','200 sheets','250 sheets']),
 ('garbage bags', 'Medium size bin liners', ['10 pcs','20 pcs','30 pcs','40 pcs','50 pcs']),
 ('Samsung charger', 'USB C wall adapter', ['15W','20W','25W','35W','45W']),
 ('usb cable', 'Braided USB A to C cable', ['0.5m','1m','1.5m','2m','3m']),
 ('Boat earbuds', 'Wireless Bluetooth earbuds', ['black','white','blue','green','red']),
 ('wireless mouse', 'Optical mouse with USB receiver', ['black','white','blue','grey','red']),
 ('computer keyboard', 'Wired full size keyboard', ['black','white','blue','grey','silver']),
 ('Nike socks', 'Cotton ankle socks', ['black','white','blue','grey','red']),
 ('cotton shirt', 'Regular fit, full sleeve', ['size S','size M','size L','size XL','size XXL']),
 ('denim jeans', 'Straight fit', ['size 28','size 30','size 32','size 34','size 36']),
 ('woven scarf', 'Lightweight neck scarf', ['black','beige','blue','green','red']),
 ('leather belt', 'Adjustable buckle', ['size 30','size 32','size 34','size 36','size 38']),
 ('Milton bottle', 'Stainless steel water bottle', ['250ML','500ML','750ML','1L','1.5L']),
 ('frying pan', 'Non-stick induction compatible', ['18cm','20cm','24cm','26cm','28cm']),
 ('ceramic mug', 'Microwave safe coffee mug', ['200ML','250ML','300ML','350ML','400ML']),
 ('mixing bowl', 'Stainless steel kitchen bowl', ['500ML','1L','2L','3L','5L']),
 ('cotton towel', 'Soft bath towel', ['white','blue','green','grey','pink']),
 ('silicone spatula', 'Heat resistant cooking tool', ['red','black','green','blue','orange']),
 ('notebook ruled', 'Paper stationery, spiral bound', ['80 pages','100 pages','120 pages','160 pages','200 pages']),
]

def generate():
    rows=[]
    for title, description, variants in BASE:
        for variant in variants:
            rows.append({'sku':f'CAT-{len(rows)+1:04}', 'raw_title':f'  {title}   {variant} ', 'raw_description':description})
    for original in rows[:40]:
        rows.append({'sku':f'CAT-{len(rows)+1:04}', 'raw_title':' '.join(original['raw_title'].upper().split()), 'raw_description':original['raw_description'].upper()})
    path=Path(__file__).resolve().parents[1]/'data/sample_products.csv'
    with path.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=['sku','raw_title','raw_description']);writer.writeheader();writer.writerows(rows)
    print(f'Wrote {len(rows)} rows to {path}')

if __name__=='__main__':generate()
