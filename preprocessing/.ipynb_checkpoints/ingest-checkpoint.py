#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import dask.dataframe as dd
from .. import utils
import pandas as pd
import numpy as np
import geopandas
import shapely
class make_parquet:
    def __init__(self,cell_by_gene=None,cell_meta=None,cellpose=False,input_format='Csv'):
        """
        Parameters:
                    cell_by_gene: str, required
                        Full path to cell_by_gene.csv output by vizgen containing cell by gene matrix

                    cell_meta: str, required
                        Full path to cell_metadata.csv output by vizgen, containing cell geometry on a x, y plane.

                    cell_pose: bool, default=False
                        If the cell_by_gene and cell_meta are the output from cellpose segmentation algorithm instead of watershed.

                    input_format: str, default='Csv'
                        Specify the input format. Accepts either 'Csv' or 'Anndata' for now


        """
        assert input_format in ['Csv', 'Anndata'], "Invalid input_format. Should be one of ['Csv', 'Anndata']"
        if input_format=='Csv':
            self.cell_by_gene=dd.read_table(cell_by_gene,sep=',')
            self.cell_meta=dd.read_table(cell_meta,sep=',')
            self.cell_meta=self.cell_meta.rename(columns={self.cell_meta.columns[0]:"cell"})
            self.cell_meta=self.cell_meta.set_index(self.cell_meta.columns[0])
            self.cell_meta.index=self.cell_meta.index.rename('cell')
            # Check for existence of 'x_min', 'y_min', 'x_max', 'y_max' and handle if not present
            required_columns = ['x_min', 'y_min', 'x_max', 'y_max']
            for col in required_columns:
                if col not in self.cell_meta.columns:
                    self.cell_meta[col] = 0  # Replace 0 with an appropriate default value if necessary

        elif input_format=='Anndata':
            import anndata
            self.anndata=anndata.read_h5ad(cell_by_gene)
            self.cell_by_gene=pd.DataFrame(self.anndata.X,index=self.anndata.obs['cell'],columns=self.anndata.var_names)
            self.cell_by_gene= dd.from_pandas(self.cell_by_gene,chunksize=10000,sort=False)
            # Default values for 'min_x', 'min_y', 'max_x', and 'max_y'
            default_value = 0  # can set this to an appropriate value later
            meta_data_columns = ['x','y'] + ['min_x', 'min_y', 'max_x', 'max_y']
            self.cell_meta = pd.DataFrame(index=self.anndata.obs['cell'])
            for col in meta_data_columns:
                if col in self.anndata.obs.columns:
                    self.cell_meta[col] = self.anndata.obs[col]
                else:
                    # Set the default value if the column is missing
                    self.cell_meta[col] = default_value
            self.cell_meta=dd.from_pandas(self.cell_meta,chunksize=10000,sort=False)
        self.prefilt_cell_sum=utils._sum.sum_per_cell(self.cell_by_gene,exclude_blanks=True)
        self.prefilt_trx_sum=utils._sum.sum_per_trx(self.cell_by_gene)
        self.prefilt_cell_num=len(self.prefilt_cell_sum)
    def filt(self,lower_threshold=0,upper_threshold=300, output_name='filt/',output_fmt='parquet',output_name_prefix='filt'):
        """
        Parameters: lower_threshold: int (optional),default = 0

                    upper_threshold: int (optional),default = 3000

                    output_name: str (optional), default=None
                        the output path where the filtered parquet file will be written.
                        If not set, then filtered output is not written to disc.

                    output_fmt: {'parquet','csv'}, default='parquet'
                        This defines the format of the filtered dataframes. If parquet, parquet files and their metadata will be written to the output path. This is the default. If csv, a csv file, in which rows are cells and columns are geometry information and abundance values of each transcript, is written.

                    output_name_prefix: str (optional), default = 'filt'
                        the output prefix for file name to be written
        """
        self.lower_threshold=lower_threshold
        self.upper_threshold=upper_threshold
        indx_l=self.prefilt_cell_sum[(self.prefilt_cell_sum>self.lower_threshold)&(self.prefilt_cell_sum<self.upper_threshold)].index.tolist()
        self.cell_by_gene_filt=self.cell_by_gene.loc[indx_l,:]
        self.cell_meta_filt=self.cell_meta.loc[indx_l,:]
        self.filt_merge_=self.cell_meta_filt.join(self.cell_by_gene_filt,on='cell')
        if not os.path.exists(output_name):
            os.mkdir(output_name)
        if output_fmt=='parquet':
            self.filt_merge_.to_parquet(f'{output_name}',compute=True,name_function=lambda x: f"{output_name_prefix}-{x}.parquet")
        elif output_fmt=='csv':
            self.filt_merge_.to_csv(f'{output_name}/{output_name_prefix}.csv',single_file=True,compute=True,sep='\t')
        self.c_sum=utils._sum.sum_per_cell(self.cell_by_gene_filt)
        self.t_sum=utils._sum.sum_per_trx(self.cell_by_gene_filt)
        self.cell_num=len(self.c_sum)
    def write_metric(self,output_name='metric.txt'):
        """
        Parameters:
                    output_name: str (optional),default='metric.txt'

        """
        df=pd.concat((self.prefilt_trx_sum,self.t_sum),axis=1)
        df.columns=['pre-filt','post-filt']
        df.sort_values(by='pre-filt',ascending=False,inplace=True)
        median_trx_per_cell=int(np.median(self.prefilt_cell_sum))
        with open (output_name,'w') as f:
            f.write(f"Number of cells (prefilter): {self.prefilt_cell_num}\n")
            f.write(f"Number of cells (postfilter): {self.cell_num}\n")
            f.write(f"Median transcripts per cell (prefilter): {median_trx_per_cell}\n\n")
            f.write("numbers of transcripts pre- and post- filter:\n")
            f.write(df.to_string() + "\n\n")


class read_parquet:
    def __init__(self,filt_path, n_meta_col=21):
        '''
        Parameters:
                    filt_path: str, {'/path/to/csv/filt.csv','/path/to/parquet'} ,required

                    n_meta_col:int,(optional), default=21
                    the number of columns containing cell meta data. The columns after the meta data columns should be the gene transcript columns.
        '''
        try:
            if filt_path.endswith('.csv') and os.path.isfile(filt_path):
                self.parquet=dd.read_csv(filt_path,sep='\t')
            elif os.path.isdir(filt_path):
                self.parquet=dd.read_parquet(filt_path,engine='pyarrow') #fastparquet would fail for reasons unknown.
            #make a shapely geometry column of bounding boxes
            _1=self.parquet[['min_x','min_y']].compute().values
            _2=self.parquet[['min_x','max_y']].compute().values
            _3=self.parquet[['max_x','max_y']].compute().values
            _4=self.parquet[['max_x','min_y']].compute().values
            _5=np.concatenate((_1,_2,_3,_4,_1),axis=1).reshape([len(_1),5,2])
            self.geometry=geopandas.GeoDataFrame({'cell':self.parquet.index.compute().to_numpy(),'geometry':list(shapely.geometry.Polygon(i) for i in (_5))}).set_index('cell',drop=True)
            column_names=self.parquet.columns
            self._genes=column_names[(n_meta_col):]
            self._genes_no_blank=[i for i in self._genes if not i.startswith('Blank')]
        except IOError as e:
            print ( 'is filt_path the full path to either filt.csv or a parquet folder generated by make_parquet')
        except Exception:
            print (Exception)
