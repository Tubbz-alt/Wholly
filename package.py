# TODO: Check imports

import os
import sys
import urlparse
import posixpath
import logging

import yaml

import image
import constants as cst

#from pykwalify.core import Core
#from pykwalify.errors import PyKwalifyException

class Package:
    def __init__(self, package_name, recipe_file_contents, contents_file_contents, args):
        # TODO: Validate package yaml file against schema
        #try:
        #    c = Core(source_data=yamlContents, schema_files=[os.path.join(os.getcwd(), 'tool/schemas/package.yaml')])
        #    logging.getLogger('pykwalify.core').setLevel(logging.CRITICAL)
        #    c.validate(raise_exception=True)
        #except PyKwalifyException as e:
        #    logging.error('For package ' + packageName + '-' + packageVersion + ' -> ' + e.msg)
        #    sys.exit(1)

        self.package_name = package_name
        self.args = args
        self.parse_recipe_file(yaml.load(recipe_file_contents))
        if contents_file_contents:
            self.subpackages_contents = yaml.load(contents_file_contents)
        else:
            self.subpackages_contents = {}
        self.dockerfile = None

    def parse_recipe_file(self, recipe_file_contents):
        self.variables = recipe_file_contents.pop('variables', {})
        self.source = recipe_file_contents.pop('source', None)
        self.resources = recipe_file_contents.pop('resources', None)
        self.prepare_commands = recipe_file_contents.pop('prepare', None)
        self.build_commands = recipe_file_contents.pop('build', None)
        self.dependencies = recipe_file_contents.pop('dependencies', None)
        self.revision = recipe_file_contents.pop('revision', None)

        self.release_date = recipe_file_contents.pop('release_date', None)
        if not self.release_date:
            logging.error('Package '+self.package_name+' does not provide release date. Stopping.')
            sys.exit(1)
        self.release_date = self.release_date.__format__('%Y%m%d%H%M')

        if self.variables:
            self.variables = {k: v for d in self.variables for k, v in d.items()}

        ## Add global variables to parsing
        self.variables['__RES_DIR__'] = '/build'
        self.variables['__INSTALL_DIR__'] = '/usr'
        self.variables['__SRC_DIR__'] = '/build/' + self.package_name
        self.variables['__NB_CORES__'] = self.args['nb_cores']

    def get_build_dependencies(self):
        if self.dependencies:
            return self.dependencies.keys()
        else:
            return []

    def write_df_line(self, str):
        self.dockerfile.write(str + '\n')

    def write_df_newline(self):
        self.write_df_line('')

    def write_df_multiline_args(self, prefix, args, separator=''):
        nargs = len(args)
        if nargs == 1:
            self.write_df_line(prefix + ' ' + args[0])
        else:
            for i in range(nargs):
                if i == 0:
                    self.write_df_line(prefix + ' ' + args[0] + ' ' + separator + ' \\')
                elif i == nargs - 1:
                    self.write_df_line('  ' + args[i])
                else:
                    self.write_df_line('  ' + args[i] + ' ' + separator + ' \\')

    def write_df_comment(self, str):
        self.write_df_line('# ' + str)

    def write_df_base_part(self, img_name, stage_name=None):
        df_line = 'FROM ' + img_name
        if stage_name:
            df_line = df_line + ' as ' + stage_name
        self.write_df_line(df_line)
        self.write_df_newline()

    def write_df_deps_base_part(self):
        if not self.dependencies:
            return
        for pkg_name in self.dependencies:
            for subpkg_name in self.dependencies[pkg_name]:
                img_name = image.get_package_image_name(pkg_name, subpkg_name)
                self.write_df_base_part(img_name, img_name+'-files')

    def write_df_bring_deps_files(self):
        if not self.dependencies:
            return
        self.write_df_comment('Bringing dependencies in')
        for pkg_name in self.dependencies:
            for subpkg_name in self.dependencies[pkg_name]:
                img_name = image.get_package_image_name(pkg_name, subpkg_name)
                stage_name = img_name+'-files'
                self.write_df_line('COPY --from='+stage_name+' / /')
        self.write_df_newline()

    def write_df_copy_res_part(self):
        if not self.resources:
            return
        self.write_df_comment('Copying resources')
        for res_name in self.resources:
            res_path = os.path.join(cst.PATH_RES_DIR, res_name)
            self.write_df_line('COPY ' + res_path + ' /build')
        self.write_df_newline()

    def write_df_prep_part(self):
        if not self.prepare_commands:
            return
        self.write_df_comment('Preparing')
        self.write_df_line('WORKDIR /build')
        for prep_cmd in self.prepare_commands:
            prep_cmd = prep_cmd.format(**self.variables)
            self.write_df_line('RUN ' + prep_cmd)
        self.write_df_newline()

    def write_df_get_source_part(self):
        self.write_df_comment('Getting source')
        source_dir = self.get_package_name()
        self.write_df_line('WORKDIR /build')
        if 'git' in self.source:
            repo_src = self.source['git'].format(**self.variables)
            self.write_df_line('RUN git clone ' + repo_src + ' ' + source_dir)
        elif 'tar.gz' in self.source:
            tar_src = self.source['tar.gz'].format(**self.variables)
            self.write_df_line('RUN curl "' + tar_src + '" -o src.tar.gz')
            self.write_df_line('RUN mkdir ' + source_dir + ' && tar xf src.tar.gz -C ' + source_dir + ' --strip-components 1')
        elif 'tar.bz2' in self.source:
            tar_src = self.source['tar.bz2'].format(**self.variables)
            self.write_df_line('RUN curl "' + tar_src + '" -o src.tar.bz2')
            self.write_df_line('RUN mkdir ' + source_dir + ' && tar xf src.tar.bz2 -C ' + source_dir + ' --strip-components 1')
        elif 'tgz' in self.source:
            tar_src = self.source['tgz'].format(**self.variables)
            self.write_df_line('RUN curl "' + tar_src + '" -o src.tgz')
            self.write_df_line('RUN mkdir ' + source_dir + ' && tar xf src.tgz -C ' + source_dir + ' --strip-components 1')
        self.write_df_newline()

    def write_df_build_part(self):
        if not self.build_commands:
            return
        self.write_df_comment('Building')
        self.write_df_line('WORKDIR ' + os.path.join('/build', self.get_package_name()))
        for build_cmd in self.build_commands:
            build_cmd = build_cmd.format(**self.variables)
            self.write_df_line('RUN ' + build_cmd)
        self.write_df_newline()

    def write_df_copy(self, src_path, dest_path, build_stage):
        self.write_df_line('COPY --from='+build_stage+' '+src_path+' '+dest_path)

    def write_build_dockerfile(self, df_file):
        self.dockerfile = df_file
        self.write_df_deps_base_part()
        self.write_df_base_part(image.get_base_image_name())
        self.write_df_bring_deps_files()
        self.write_df_copy_res_part()
        self.write_df_prep_part()
        self.write_df_get_source_part()
        self.write_df_build_part()
        self.dockerfile.flush()
        self.dockerfile.close()

    def write_subpackage_dockerfile(self, df_file, contents_list, subpkg_name):
        build_files_stage_name = 'build'
        self.dockerfile = df_file

        # Packaging files
        self.write_df_base_part(image.get_package_image_name(self.package_name), build_files_stage_name)
        self.write_df_line('COPY '+cst.PATH_TMP_CONTENTS_FILE+' '+cst.PATH_TMP_CONTENTS_FILE)
        self.write_df_line('COPY '+cst.PATH_TMP_ARTIFACTS_FILE+' '+cst.PATH_TMP_ARTIFACTS_FILE)
        self.write_df_line('RUN mkdir -p '+cst.RELEASE_DIRECTORY)
        if contents_list:
            if subpkg_name == 'bin':
                # Try to strip binaries automatically
                self.write_df_line('RUN while read p; do strip "$p" || true; done < '+cst.PATH_TMP_CONTENTS_FILE)
            self.write_df_line('RUN while read p; do cp --parents -r "$p" '+cst.RELEASE_DIRECTORY+'; done < '+cst.PATH_TMP_CONTENTS_FILE)
            self.write_df_line('RUN while read p; do touch -t '+self.release_date+' "$p"; done < '+cst.PATH_TMP_ARTIFACTS_FILE)
        self.write_df_line('RUN rm '+cst.PATH_TMP_CONTENTS_FILE)

        # Releasing
        self.write_df_base_part('scratch')
        self.write_df_line('COPY --from='+build_files_stage_name+' '+cst.RELEASE_DIRECTORY+' /')
        self.dockerfile.flush()
        self.dockerfile.close()

    def get_package_name(self):
        return self.package_name

    def get_subpackages_contents(self):
        return self.subpackages_contents

    def get_subpackages_artifacts(self):
        artifacts = {}
        for key, files in self.subpackages_contents.iteritems():
            artifacts[key] = map(lambda x: cst.RELEASE_DIRECTORY + x, self.subpackages_contents[key])
            for f in files:
                dirname = cst.RELEASE_DIRECTORY + os.path.dirname(f)
                while dirname != '/':
                    if not dirname in artifacts[key]:
                        artifacts[key].append(dirname)
                    dirname = os.path.dirname(dirname)
        return artifacts
