import csv
import sqlite3 as sqlite
import urllib.request
import re
import bs4
import argparse
import os
import json
from collections import Counter
from itertools import tee, count


def uniquify(seq, suffs = count(1)):
    """Make all the items unique by adding a suffix (1, 2, etc).

    `seq` is mutable sequence of strings.
    `suffs` is an optional alternative suffix iterable.
    """
    not_unique = [k for k,v in Counter(seq).items() if v>1] # so we have: ['name', 'zip']
    # suffix generator dict - e.g., {'name': <my_gen>, 'zip': <my_gen>}
    suff_gens = dict(zip(not_unique, tee(suffs, len(not_unique))))
    for idx,s in enumerate(seq):
        try:
            suffix = str(next(suff_gens[s]))
        except KeyError:
            # s was unique
            continue
        else:
            seq[idx] += suffix


def load_veg(con, csvpath):
    # con = sqlite.connect(db)
    c = con.cursor()
    i = con.cursor()
    print("loading ecosite veg rows into database...")
    c.execute("CREATE TABLE veg (site_id TEXT PRIMARY KEY, veg_sci TEXT)")
    with open(csvpath, newline='', encoding='utf8') as f:
        reader = csv.DictReader(f, delimiter='|')
        for row in reader:
            c.execute("INSERT OR IGNORE INTO veg (site_id, veg_sci) VALUES (?,?);", (row['site_id'], row['veg_sci']))
            con.commit()
    c.execute("CREATE TABLE veg_split (site_id TEXT, order_1 INTEGER, order_2 INTEGER, veg_group TEXT, sci_name TEXT);")
    con.commit()

    print("splitting ecosite veg and updating new table (veg_split)...")
    rows = c.execute("SELECT site_id, veg_sci FROM veg ORDER BY site_id;")
    for row in rows:
        site_id = row[0]
        veg = row[1]
        veg_list = [x.split('-') for x in veg.split('/')]
        order_1 = 1
        # print(veg_list)
        for sub_list in veg_list:
            if order_1 == 1:
                group = 'tree'
            elif order_1 == 2:
                group = 'shrub'
            elif order_1 == 3:
                group = 'grass'
            else:
                group = 'unknown'
            order_2 = 1
            for s in sub_list:
                if s.strip():
                    i.execute("INSERT INTO veg_split (site_id, order_1, order_2, veg_group, sci_name) "
                              "VALUES (?,?,?,?,?);", (site_id, order_1, order_2, group, s.strip()))
                    # print(site_id, order_1, order_2, group, s.strip())
                    order_2 += 1
                    con.commit()
            order_1 += 1
    c.execute("UPDATE veg_split SET sci_name = REPLACE(sci_name, ' subsp. ', ' ssp. ');")
    con.commit()
    # con.close()


# deprecated as of USDA plants update circa 2021
def grab_usda_plants(con):
    # con = sqlite.connect(db)
    c = con.cursor()
    i = con.cursor()
    print("getting PLANTS list from plants.sc.egov.usda.gov")
    # get plant list from USDA in order to replace scientific names with codes
    link = r'https://plants.sc.egov.usda.gov/java/AdvancedSearchServlet?dsp_vernacular=on&dsp_family=on&dsp_dur=on&' \
           r'dsp_grwhabt=on&dsp_nativestatuscode=on&Synonyms=all&dsp_synonyms=on&dsp_authorname_separate=on&' \
           r'viewby=sciname'
    r = urllib.request.urlopen(link).read()
    soup = bs4.BeautifulSoup(r, 'lxml')
    a = soup.find_all('a', text=re.compile('^Download$'))
    if a:
        txt_link = ''.join((r'https://plants.sc.egov.usda.gov/java/', a[0]['href']))
    data = urllib.request.urlopen(txt_link).read()
    text = data.decode('utf-8')
    lines = text.split('\n')
    del soup, text, data

    print("loading PLANTS list into database...")
    header = lines[0]
    heads = header.split(',')
    heads = [x.replace('"', '') for x in heads]
    heads = [re.sub('[^0-9a-zA-Z]+', '_', x).lower() for x in heads]
    uniquify(heads, (f'_{x!s}' for x in range(1, 100)))  # makes sure field names are unique in case of duplicates
    sql_heads = [' '.join((x, "TEXT")) for x in heads]
    sql = "CREATE TABLE plants ({!s});".format(', '.join(sql_heads))
    c.execute(sql)
    con.commit()
    isql = "INSERT INTO plants ({!s}) VALUES ({!s})".format(', '.join(heads), ', '.join('?' * len(sql_heads)))
    for l in lines[1:]:
        if l:
            l_list = l.split('","')
            l_list = [x.replace('"', '') for x in l_list]
            l_list = [x if x != '' else None for x in l_list]
            # print(len(l_list), l_list)
            i.execute(isql, l_list)
    con.commit()
    # con.close()


def grab_plants(con, plant_db):
    c = con.cursor()
    c.execute("""
    CREATE TABLE plants(
        symbol TEXT PRIMARY KEY,
        accepted_symbol TEXT,
        synonym_symbol TEXT,
        scientific_name TEXT,
        hybrid_genus_indicator TEXT,
        genus TEXT,
        hybrid_species_indicator TEXT,
        species TEXT,
        subspecies_prefix TEXT,
        hybrid_subspecies_indicator TEXT,
        subspecies TEXT,
        variety_prefix TEXT,
        hybrid_variety_indicator TEXT,
        variety TEXT,
        subvariety_prefix TEXT,
        subvariety TEXT,
        forma_prefix TEXT,
        forma TEXT,
        genera_binomial_author TEXT,
        trinomial_author TEXT,
        quadranomial_author TEXT,
        questionable_taxon_indicator TEXT,
        parents TEXT,
        common_name TEXT,
        state_and_province TEXT,
        family TEXT,
        duration TEXT,
        growth_habit TEXT,
        native_status TEXT);
    """)
    c.execute(f"ATTACH '{plant_db}' AS plant;")
    c.execute("INSERT INTO main.plants SELECT * FROM plant.plants;")
    # c.execute("DETACH plant;")
    con.commit()


def replace_veg_code(con):
    # con = sqlite.connect(db)
    c = con.cursor()
    print("recreating veg list with codes replacing scientific names...")
    # combines subgroups into single string
    sql = """
    CREATE TABLE subgroup AS
    SELECT site_id, order_1, veg_group, group_concat(accepted_symbol, '-') AS subgroup
      FROM (
           SELECT a.*, coalesce(b.accepted_symbol, a.sci_name) AS accepted_symbol
             FROM veg_split AS a
             LEFT JOIN (
                  SELECT scientific_name, accepted_symbol 
                    FROM plants 
                   GROUP BY scientific_name, accepted_symbol
                  ) AS b ON lower(a.sci_name) = lower(b.scientific_name)
            ORDER BY site_id, order_1, order_2
           ) AS x
     GROUP BY site_id, order_1, veg_group;
     """
    c.execute(sql)
    con.commit()
    # add missing groups (tree, grass, shrub) so final string is formatted correctly
    c.execute("CREATE TABLE group_orders (order_1 INTEGER, veg_group TEXT);")
    c.executemany("INSERT INTO group_orders (order_1, veg_group) VALUES (?, ?);", [(1, 'tree'), (2, 'shrub'), (3, 'grass')])
    sql = """
    CREATE TABLE veg_new AS
    SELECT site_id, group_concat(subgroup, '/') AS veg
      FROM (
           SELECT x.site_id, x.order_1, x.veg_group, coalesce(y.subgroup, '') AS subgroup
             FROM (
                  SELECT a.site_id, b.order_1, b.veg_group
                    FROM (SELECT site_id FROM subgroup GROUP BY site_id) AS a, group_orders AS b
                  ) AS x
            LEFT JOIN subgroup AS y ON x.site_id = y.site_id AND
                                       x.order_1 = y.order_1 AND
                                       x.veg_group = y.veg_group
           ORDER BY x.site_id, x.order_1) AS c
     GROUP BY site_id
     ORDER BY site_id;
    """
    c.execute(sql)
    c.execute("UPDATE veg_new SET veg = rtrim(replace(veg, '//', '/'), '/')")  # does some final formatting work
    con.commit()
    # con.close()


if __name__ == "__main__":
    """Will take the output csv from grab_names.py, and process the veg names to codes instead."""
    parser = argparse.ArgumentParser()
    parser.add_argument('csvpath', help='the file path for a csv output produced by the grab_names.py script')
    parser.add_argument('outfile', help='file path to which the converted data will be saved (.csv, .json)')
    parser.add_argument('plant_db', help='file path to sqlite db with PLANTS info.')
    parser.add_argument('-d', '--db', help='the file path to the sqlite database to which the processing results will '
                                           'be saved', default=':memory:')
    args = parser.parse_args()

    if not os.path.isfile(args.csvpath):
        print(args.csvpath, 'is not a file. Please choose a csv file produced from the grab_names.py script.')
        quit()

    con = sqlite.connect(args.db)
    c = con.cursor()
    load_veg(con, args.csvpath)
    # grab_usda_plants(con)
    grab_plants(con, args.plant_db)
    replace_veg_code(con)

    print("writing results to file...")
    rows = c.execute("SELECT * FROM veg_new ORDER BY site_id;")
    fieldnames = [x[0] for x in c.description]
    if os.path.splitext(args.outfile)[1] == '.csv':
        with open(args.outfile, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile, delimiter='|')
            writer.writerow(fieldnames)
            for row in rows:
                writer.writerow(row)
    elif os.path.splitext(args.outfile)[1] == '.json' or not os.path.splitext(args.outfile)[1]:
        results = []
        for row in rows:
            results.append({fieldnames[0]: row[0], fieldnames[1]: row[1]})
        with open(args.outfile, 'w', encoding='utf-8') as jsonfile:
            json.dump(results, jsonfile, ensure_ascii=False, indent=4)

    con.close()
    print('\nScript finished.\n')

