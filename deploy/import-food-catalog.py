#!/usr/bin/env python3
"""Reproducible Russian short names; original descriptions/IDs remain in the JSON."""
import csv,io,json,zipfile,bisect
from pathlib import Path
from decimal import Decimal,ROUND_HALF_UP
import argparse
parser=argparse.ArgumentParser(description='Build the curated offline catalogue from the official USDA SR Legacy CSV archive')
parser.add_argument('archive', type=Path)
args=parser.parse_args()
root=Path(__file__).resolve().parent.parent
z=zipfile.ZipFile(args.archive)
def rows(name):
 return csv.DictReader(io.TextIOWrapper(z.open(next(n for n in z.namelist() if n.endswith('/'+name))),encoding='utf-8-sig'))
foods={r['fdc_id']:r for r in rows('food.csv')}
values={}
fields={'1008':'kcal_per_100g','1003':'protein_per_100g','1004':'fat_per_100g','1005':'carbs_per_100g'}
for r in rows('food_nutrient.csv'):
 if r['nutrient_id'] in fields:values.setdefault(r['fdc_id'],{})[fields[r['nutrient_id']]]=str(Decimal(r['amount']).quantize(Decimal('.01')))
entries=[]
def add(name,category,identifier,aliases=(),note=''):
 if identifier.isdigit(): f=foods[identifier]
 else:
  matches=[f for f in foods.values() if f['description'].lower()==identifier.lower()]
  if len(matches)!=1:
   raise ValueError('Missing or ambiguous food: '+name+' / '+identifier)
  f=matches[0]
 nutrients=values.get(f['fdc_id'],{})
 if len(nutrients)!=4: raise ValueError('Missing nutrients: '+name)
 entries.append({'id':'usda-'+f['fdc_id'],'name':name,'category':category,'aliases':list(aliases),'unit':'г',**nutrients,'nutrition_source':'estimate','macros_source':'estimate','note':note,'source':{'dataset':'USDA SR Legacy','fdc_id':f['fdc_id'],'description':f['description'],'url':'https://fdc.nal.usda.gov/food-details/'+f['fdc_id']+'/nutrients'}})
def group(cat,data,note='Свежий продукт'):
 for line in data.strip().splitlines():
  parts=line.split('|');add(parts[0],cat,parts[1],parts[2].split(',') if len(parts)>2 else (),note)
group('Фрукты','''Яблоко|171688|яблоки,яблочко
Груша|169118|груши
Банан|173944|бананы
Апельсин|169097|апельсины
Мандарин|169105|мандарины
Грейпфрут|173033
Лимон|167746
Лайм|168155
Киви|168153
Персик|169928|персики
Нектарин|169914
Абрикос|171697|абрикосы
Слива|169949|сливы
Ананас|169124
Манго|169910
Гранат|169134
Хурма|169941
Арбуз|167765
Дыня|169092
Авокадо|171705
Виноград|174683
Папайя|169926
Инжир|173021
Айва|168163
Фейхоа|168176
Кумкват|168154
Клементин|168195
Маракуйя|169108
Личи|169086
Гуава|173044''')
group('Ягоды','''Клубника|167762|клубничка
Малина|167755
Ежевика|173946
Голубика|171711|черника
Клюква|171722
Крыжовник|173030
Смородина чёрная|173963|черная смородина
Смородина красная|173964|красная смородина
Вишня|173954
Черешня|171719
Шелковица|169913''')
group('Овощи','''Огурец|168409|огурцы
Помидор|Tomatoes, red, ripe, raw, year round average|томат,помидоры
Морковь|Carrots, raw
Капуста|169975|белокочанная капуста
Капуста красная|169977|краснокочанная капуста
Капуста пекинская|169979|пекинка
Цветная капуста|169986
Брокколи|Broccoli, raw
Перец сладкий|Peppers, sweet, red, raw|болгарский перец
Перец жёлтый|169383
Редис|169276|редиска
Дайкон|168451
Свёкла|169145|свекла
Лук|Onions, raw|репчатый лук
Лук зелёный|Onions, spring or scallions (includes tops and bulb), raw|зеленый лук
Чеснок|169230
Кабачок|169291|цуккини
Баклажан|169228
Тыква|168448
Сельдерей|169988
Шпинат|168462
Салат листовой|169249|листья салата
Салат айсберг|169248
Руккола|169387
Петрушка|Parsley, fresh
Укроп|Dill weed, fresh
Кинза|169997|кориандр
Имбирь|169231
Лук-порей|169246
Спаржа|168389
Кольраби|168424
Репа|Turnips, raw
Брюква|168454
Батат|168482
Фенхель|169385
Шампиньоны|169251|грибы''')
group('Овощи','''Картофель|Potatoes, boiled, cooked without skin, flesh, without salt|картошка,вареный картофель
Морковь варёная|Carrots, cooked, boiled, drained, without salt|вареная морковь
Свёкла варёная|Beets, cooked, boiled, drained|вареная свекла
Брокколи варёная|Broccoli, cooked, boiled, drained, without salt
Цветная капуста варёная|Cauliflower, cooked, boiled, drained, without salt
Капуста варёная|Cabbage, cooked, boiled, drained, without salt
Кабачок варёный|Squash, summer, zucchini, includes skin, cooked, boiled, drained, without salt
Тыква варёная|Pumpkin, cooked, boiled, drained, without salt
Стручковая фасоль|Beans, snap, green, cooked, boiled, drained, without salt
Зелёный горошек|Peas, green, cooked, boiled, drained, without salt
Кукуруза|Corn, sweet, yellow, cooked, boiled, drained, without salt''','Варёный продукт, без масла')
group('Крупы и гарниры','''Гречка|170686|гречневая каша,гречневая крупа,греча
Рис|168878|рисовая каша,белый рис
Рис бурый|168875|коричневый рис
Рис дикий|168897
Пшено|168871|пшенная каша,пшенка
Перловка|170285|перловая каша,перловая крупа
Киноа|168917
Булгур|Bulgur, cooked
Кускус|Couscous, cooked|кус кус
Овсянка|173905|овсяная каша,геркулес
Макароны|168928|паста
Макароны цельнозерновые|168910
Рисовая лапша|168914
Соба|168907''','Варёное на воде, без масла; вес готового продукта')
group('Бобовые','''Чечевица|Lentils, mature seeds, cooked, boiled, without salt
Нут|Chickpeas (garbanzo beans, bengal gram), mature seeds, cooked, boiled, without salt|турецкий горох
Фасоль красная|Beans, kidney, red, mature seeds, cooked, boiled, without salt
Фасоль белая|Beans, white, mature seeds, cooked, boiled, without salt
Горох|Peas, split, mature seeds, cooked, boiled, without salt''','Варёное на воде, без масла')
group('Орехи и семечки','''Миндаль|170567
Грецкий орех|170187|грецкие орехи
Фундук|170581|лесной орех
Кешью|170162
Фисташки|170184
Кедровые орехи|170591
Пекан|170182
Бразильский орех|170569
Макадамия|170178
Кунжут|170150
Семечки подсолнечника|170562|семечки
Тыквенные семечки|170556
Семена льна|169414
Семена чиа|170554''','Без сахара, съедобная часть')
group('Сухофрукты','''Курага|Apricots, dried, sulfured, uncooked
Чернослив|Plums, dried (prunes), uncooked
Изюм|168165
Финики|Dates, deglet noor
Инжир сушёный|Figs, dried, uncooked
Яблоки сушёные|Apples, dried, sulfured, uncooked''','Без добавленного сахара')
group('Молочные продукты','''Йогурт натуральный|171284
Йогурт нежирный|170886
Йогурт обезжиренный|170887
Сыр фета|Cheese, feta
Сыр моцарелла|Cheese, mozzarella, whole milk
Сыр чеддер|173414
Сыр пармезан|Cheese, parmesan, hard
Масло сливочное|173430
Сливки 12%|171255
Сливки 36%|170859''','Усреднённое значение')
group('Масла и прочее','''Масло оливковое|Oil, olive, salad or cooking
Масло подсолнечное|171025
Масло кокосовое|Oil, coconut
Сахар|Sugars, granulated
Мёд|Honey
Вода|Beverages, water, tap, drinking''','')
group('Яйца, мясо и рыба','''Яйцо|Egg, whole, cooked, hard-boiled|яйца,вареное яйцо
Куриная грудка|Chicken, broilers or fryers, breast, meat only, cooked, stewed|куриное филе,курица
Индейка|Turkey, whole, breast, meat only, cooked, roasted
Лосось|Fish, salmon, Atlantic, farmed, cooked, dry heat|семга,сёмга
Треска|Fish, cod, Atlantic, cooked, dry heat
Минтай|173726
Креветки|171971
Тунец|173709''','Готовый продукт; съедобная часть')
group('Хлеб','''Хлеб пшеничный|Bread, white, commercially prepared (includes soft bread crumbs)|белый хлеб
Хлеб ржаной|Bread, rye|черный хлеб,чёрный хлеб
Хлеб цельнозерновой|Bread, whole-wheat, commercially prepared
Пита|Bread, pita, white, unenriched
Багет|Bread, french or vienna (includes sourdough)''','Усреднённое значение')
# Dairy fat percentages are explicitly estimated by interpolation of unfortified USDA milk.
points=[(Decimal('0'), '173432'),(Decimal('1'),'173441'),(Decimal('2'),'172205'),(Decimal('3.25'),'172217'),(Decimal('3.7'),'171266')]
for fat in map(Decimal,['0','0.5','1','1.5','2','2.5','3.2','3.5']):
 i=max(1,min(len(points)-1,bisect.bisect_left([p[0] for p in points],fat)))
 a,b=points[i-1],points[i];t=(fat-a[0])/(b[0]-a[0]);nutrients={k:str((Decimal(values[a[1]][k])*(1-t)+Decimal(values[b[1]][k])*t).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)) for k in fields.values()}
 nutrients['fat_per_100g']=str(fat)
 entries.append({'id':'milk-'+str(fat).replace('.','-'),'name':'Молоко '+str(fat).replace('.',',')+'%','category':'Молочные продукты','aliases':['молоко '+str(fat)+'%'],'unit':'г',**nutrients,'nutrition_source':'estimate','macros_source':'estimate','note':'Усреднённое значение; на 100 г','source':{'dataset':'USDA SR Legacy','fdc_ids':[a[1],b[1]],'derivation':'Linear interpolation by fat percentage; approximate','url':'https://fdc.nal.usda.gov/'}})
assert len({e['id'] for e in entries})==len(entries)
# Blueberries and bilberries are distinct; do not advertise them as the same fruit.
next(e for e in entries if e['name']=='Голубика')['aliases']=[]
catalog={'version':1,'updated_at':'2026-10-07','source_url':'https://fdc.nal.usda.gov/download-datasets/','notice':'Справочные усреднённые значения USDA; молоко промежуточной жирности — оценка. Крупы только варёные на воде без масла. Все значения на 100 г съедобной части.','products':entries}
(root/'mobile/assets/food_catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,separators=(',',':'))+'\n')

print('CATALOG',len(entries),'products',(root/'mobile/assets/food_catalog.json').stat().st_size,'bytes')
