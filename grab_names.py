import bs4
import re
import os
import csv
import json
import sys
import argparse
import pandas as pd
from bs4 import Tag, NavigableString
from io import StringIO


def get_parent_table(elem):
    """Returns the parent table of the element passed to it."""
    # print(elem.parent.name)
    if elem.parent.name == 'table':
        # print("found parent")
        return elem.parent
    else:
        return get_parent_table(elem.parent)


def compile_species(elem, some_str=''):
    """Compiles a species list from a very specific part of the 'esddetail' table"""
    if elem.string:
        some_str += elem.string.strip()
        # print('some_str now:', some_str)
    if isinstance(elem.next_sibling, Tag):
        # print("tag:", elem.next.name)
        if elem.next_sibling.name == 'br':
            some_str += '\n'
        if elem.next_sibling.attrs.get('class'):
            # print('class:', elem.next_sibling.attrs.get('class'))
            if elem.next_sibling.attrs.get('class')[0] == 'esdtag2':
                return some_str.replace(' / ', '/').replace(' - ', '-').strip()
    return compile_species(elem.next_sibling, some_str)


def grab_eco_attr(file):
    """Will take an Ecological Site Description file in html format downloaded from ESIS and grab metadata. Returns
    a dictionary."""
    ecodict = None
    typeloc_df = None
    species_df = None
    total_df = None
    # with open(file, 'rb') as f:
    #     content = f.read()
    #     suggestion = bs4.UnicodeDammit(content)
    #     enc = suggestion.original_encoding
    enc1 = 'WINDOWS-1252'
    enc2 = 'latin-1'
    try:
        soup = bs4.BeautifulSoup(open(file, encoding=enc1), 'lxml')
    except UnicodeDecodeError:
        print('\tOpening file in ', enc1, ' failed. Using ', enc2, '...', sep='')
        soup = bs4.BeautifulSoup(open(file, encoding=enc2), 'lxml')
    start = soup.find_all(string=re.compile("Site stage:"))
    if start:
        start_table = get_parent_table(start[0])
        ecodict = dict()
        tag = start_table.find_all(attrs={'class': 'esdtag3Italicized'})
        for t in tag:
            if t.get_text().strip() == 'Site stage:':
                if t.find_next_sibling().name == 'span':
                    ecodict['site_stage'] = t.find_next_sibling().get_text().strip()
            elif t.get_text().strip() == 'Site name:':
                if t.find_next_sibling().name == 'b':
                    ecodict['site_name'] = t.find_next_sibling().get_text().strip()
        tag = start_table.find(attrs={'class': 'esddetaili'})
        if tag:
            veg = compile_species(tag)
            veg_list = veg.split('\n')
            if veg_list:
                ecodict['veg_sci'] = veg_list[0]
                if len(veg_list) > 1:
                    ecodict['veg_com'] = ' : '.join(veg_list[1:])
        tag = start_table.find_all(attrs={'class': 'esdtag2'})
        for t in tag:
            if t.get_text().strip() == 'Site type:':
                if isinstance(t.next_sibling, NavigableString):
                    ecodict['site_type'] = t.next_sibling.string.strip()
            elif t.get_text().strip() == 'Site ID:':
                if isinstance(t.next_sibling, NavigableString):
                    ecodict['site_id'] = t.next_sibling.string.strip()
            elif t.get_text().strip() == 'Major land resource area (MLRA):':
                if isinstance(t.next_sibling, NavigableString):
                    ecodict['mlra'] = t.next_sibling.string.strip()

    type_start = soup.find_all(string=re.compile("Type Locality"))
    if type_start:
        type_table = type_start[0].find_next("table")
        if type_table:
            type_df = pd.read_html(StringIO(str(type_table)))[0]
            type_df['loc_id'] = type_df.groupby(0).cumcount()  # in case of multiple type locs
            typeloc_df = type_df.pivot(index='loc_id', columns=0, values=1)
            typeloc_df['site_id'] = ecodict.get('site_id')
            typeloc_df['site_name'] = ecodict.get('site_name')

    plant_names = ['group', 'group_name', 'common_name', 'symbol', 'scientific_name', 
                   'prod_lbsac_low', 'prod_lbsac_high', 'foliar_pct_low', 'foliar_pct_high']

    plant_final = ['site_id', 'group_no', 'group_name', 'common_name', 'symbol', 'scientific_name', 
                   'prod_lbsac_low', 'prod_lbsac_high', 'foliar_pct_low', 'foliar_pct_high']
    plant_start = soup.find_all(string=re.compile('Plant Species Composition'))
    if plant_start:
        plant_table = plant_start[0].find_next("table")
        if plant_table:
            try:
                plant_df = pd.read_html(StringIO(str(plant_table)))[0]
            except IndexError:
                print("\tPlant Compostion table import index error.")
            else:
                if plant_df.shape[1] == 9 and 'growth' not in plant_df.iloc[0,0].lower():  # sanity check
                    plant_df.columns = plant_names
                    # fill in gaps
                    groups = plant_df['group'].tolist()
                    new_groups = []
                    for i, e in enumerate(groups):
                        if i == 0:
                            new_groups.append(e)
                        else:
                            if pd.isna(e):
                                new_groups.append(new_groups[i-1])
                            else:
                                new_groups.append(e)
                    plant_df['group'] = new_groups
                    filt_df = plant_df.query("scientific_name.isnull() == False")\
                                      .query("scientific_name.str.match('^[0-9]') == False")\
                                      .query("scientific_name != 'Scientific name'")
                    filt_df = filt_df.drop('group_name', axis = 1)
                    
                    #reindex ensures two columns are returned just in case
                    filt_df[['group_no','group_name']] = filt_df['group']\
                            .str.split('  -', expand=True)\
                            .reindex([0, 1], axis=1)
                    filt_df = filt_df.drop('group', axis = 1)
                    filt_df['site_id'] = ecodict['site_id']
                    species_df = filt_df[plant_final]
            total_table = plant_table.find_next('table')
            if total_table:
                try:
                    total_df = pd.read_html(StringIO(str(total_table)))[0]
                except IndexError:
                    print("\tPlant Total Productions table import index error.")
                else:
                    if total_df.shape[1] == 4:  # sanity check
                        total_cols = ['plant_type', 'prod_low', 'prod_rv', 'prod_high']
                        total_df.columns = total_cols
                        total_df = total_df[pd.to_numeric(total_df['prod_rv'], 
                                                          errors='coerce').notnull()]
                        total_df['site_id'] =  ecodict['site_id']
                        total_df = total_df[['site_id'] + total_cols]


    return ecodict, typeloc_df, species_df, total_df


if __name__ == "__main__":
    """Will take a search path populated with ESD files from ESIS (html) and pulls metadata from them, and populates
    either a json file or csv file."""
    parser = argparse.ArgumentParser()
    parser.add_argument('scanpath', help='path to scan for html ecosite files (from ESIS)')
    parser.add_argument('outfile', help='file path to which the scraped data will be saved (.csv or .json)')
    parser.add_argument('-t', '--type_loc', help='file path to which the type location data will be saved (.csv)')
    parser.add_argument('-s', '--species', help='file path to which the species composition data will be saved (.csv)')
    
    my_args = sys.argv[1:]
    args = parser.parse_args(my_args)  # so we can test with custom my_args if we want

    if not os.path.isdir(args.scanpath):
        print(args.scanpath, "is not a valid existing directory.")
        quit()

    print('Scanning for HTML files...')
    html_files = []
    for root, dirs, files in os.walk(args.scanpath):
        for f in files:
            if os.path.splitext(f)[1] == '.html':
                html_files.append(os.path.join(root, f))

    results = []
    typeloc_list = []
    species_list = []
    total_list = []
    start = 0
    for i in range(start, len(html_files)):
        f = html_files[i]
        print("scraping attribute data from", os.path.basename(f))
        result, loc_df, spec_df, tot_df = grab_eco_attr(f)
        if result:
            result['path'] = os.path.join(root, f)
            results.append(result)
        if loc_df is not None:
            typeloc_list.append(loc_df)
        if spec_df is not None:
            species_list.append(spec_df)
        if tot_df is not None:
            total_list.append(tot_df)



    # write out
    if os.path.splitext(args.outfile)[1] == '.csv':
        with open(args.outfile, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['path', 'site_stage', 'site_type', 'site_id', 'mlra', 'site_name', 'veg_sci', 'veg_com']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, delimiter='|')
            writer.writeheader()
            for row in results:
                writer.writerow(row)
    elif os.path.splitext(args.outfile)[1] == '.json' or not os.path.splitext(args.outfile)[1]:
        with open(args.outfile, 'w', encoding='utf-8') as jsonfile:
            json.dump(results, jsonfile, ensure_ascii=False, indent=4)
    if args.type_loc is not None:
        final_df = pd.concat(typeloc_list)
        final_df = final_df.reset_index()
        col_dict = {'site_id': 'site_id', 'site_name': 'site_name', 'loc_id': 'loc_id', 'State:': 'state',
                    'County:': 'county', 'General legal description:': 'legal_desc', 'Township:': 'township',
                    'Range:': 'range', 'Section:': 'section', 'Latitude degrees:': 'lat_deg',
                    'Latitude minutes:': 'lat_min', 'Latitude seconds:': 'lat_sec', 'Latitude decimal:': 'lat_dec',
                    'Longitude degrees:': 'long_deg', 'Longitude minutes:': 'long_min',
                    'Longitude seconds:': 'long_sec', 'Longitude decimal:': 'long_dec', 'Datum:': 'datum',
                    'Zone:': 'zone', 'Easting:': 'easting', 'Northing:': 'northing',
                    'Universal Transverse Mercator (UTM) system:': 'utm'}
        col_list = [v for k, v in col_dict.items()]
        final_df.rename(columns=col_dict, inplace=True)
        df_cols = final_df.columns.to_list()
        actual_cols = [x for x in col_list if x in df_cols]
        final_df = final_df[actual_cols]
        final_df.to_csv(args.type_loc, index_label='fid', encoding='utf-8')

    if args.species is not None:
        sp_final_df = pd.concat(species_list)
        tot_final_df = pd.concat(total_list)
        sp_final_df.to_csv(args.species, index=False, encoding='utf-8')
        total_path = args.species.replace(".csv", "_total.csv")
        tot_final_df.to_csv(total_path, index=False, encoding='utf-8')


    print('\nScript finished.\n')




