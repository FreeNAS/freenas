#!/usr/local/bin/python -R

import os, sys
import getopt
import tarfile
import json
import StringIO

kPkgNameKey = "name"
kPkgVersionKey = "version"
kPkgFilesKey = "files"
kPkgDirsKey = "directories"
kPkgRemovedFilesKey = "removed-files"
kPkgRemovedDirsKey = "removed-directories"
kPkgDeltaKey = "delta-version"

def PackageName(m):
    return m[kPkgNameKey] if kPkgNameKey in m else None

def PackageVersion(m):
    return m[kPkgVersionKey] if kPkgVersionKey in m else None

def FindManifest(tf):
    # Find the file named "+MANIFEST".
    # Also position the tarfile to be at the first non-+-named file.
    # This is annoying:  it looks like there's no way with tarfile
    # to get the current member.  So I'll make this return a list.

    retval = None
    for entry in tf:
        print >> sys.stderr, "entry %s" % entry.name
        if not entry.name.startswith("+"):
            return (retval, entry)
        if entry.name == "+MANIFEST":
            mfile = tf.extractfile(entry)
            retval = json.load(mfile)
            print "retval = %s" % retval
    return (retval, entry)

#
# Given two manifests, come up with a set of
# new or changed files/directories.  Also come
# up with a list of removed files and directories.
def CompareManifests(m1, m2):
    print "\nm1 = %s\nm2 = %s\n" % (m1, m2)
    m1_files = m1[kPkgFilesKey]
    m2_files = m2[kPkgFilesKey].copy()
    m1_dirs = m1[kPkgDirsKey]
    m2_dirs = m2[kPkgDirsKey].copy()
    retval = {}
    removed_files = []
    removed_dirs = []
    modified_files = {}
    modified_dirs = {}
    for file in m1_files.keys():
        if file not in m2_files:
            print >> sys.stderr, "File %s is removed from new package" % file
            removed_files.append(file)
        else:
            if m1_files[file] == m2_files[file]:
                if m1_files[file] == "-":
                    modified_files[file] = m1_files[file]
                m2_files.pop(file)
            else:
                modified_files[file] = m2_files[file]
    for dir in m1_dirs.keys():
        if dir not in m2_dirs:
            removed_dirs.append(dir)
        else:
            if m1_dirs[dir] != m2_dirs[dir]:
                modified_dirs[dir] = m2_dirs[dir]
            m2_dirs.pop(dir)

    # At this point, everything left in m2_files and
    # m2_dirs should be new entries
    for file in m2_files.keys():
        modified_files[file] = m2_files[file]
    for dir in m2_dirs.keys():
        modified_dirs[dir] = m2_dirs[dir]

    return { kPkgRemovedFilesKey : removed_files,
             kPkgRemovedDirsKey : removed_dirs,
             kPkgFilesKey : modified_files,
             kPkgDirsKey : modified_dirs }
    
def usage():
    print >> sys.stderr, "Usage: %s <pkg1> <pkg2> <delta_pg>" % sys.argv[0]
    sys.exit(1)

def main():
    # No options I can think of, yet anyway
    args = sys.argv[1:]

    if len(args) != 3:
        usage()

    pkg1 = args[0]
    pkg2 = args[1]
    output_file = args[2]

    pkg1_tarfile = tarfile.open(pkg1, "r")
    (pkg1_manifest, dc) = FindManifest(pkg1_tarfile)

    pkg2_tarfile = tarfile.open(pkg2, "r")
    (pkg2_manifest, member) = FindManifest(pkg2_tarfile)

    if PackageName(pkg1_manifest) != PackageName(pkg2_manifest):
        print >> sys.stderr, "Cannot diff different packages:  %s is not %s" % (
            PackageName(pkg1_manifest), PackageName(pkg2_manifest))
        sys.exit(0)

    if PackageVersion(pkg1_manifest) == PackageVersion(pkg2_manifest):
        print >> sys.stderr, "Both %s packages are version %s" % (
            PackageName(pkg1_manifest), PackageVersion(pkg1_manifest))
        sys.exit(0)

    # Everything in the p2 goes into new.
    # Except for the files and directories keys.
    new_manifest = pkg2_manifest.copy()
    new_manifest.pop(kPkgFilesKey)
    new_manifest.pop(kPkgDirsKey)
    new_manifest[kPkgDeltaKey] = PackageVersion(pkg1_manifest)

    diffs = CompareManifests(pkg1_manifest, pkg2_manifest)

    if len(diffs[kPkgRemovedFilesKey]) != 0:
        new_manifest[kPkgRemovedFilesKey] = list(diffs[kPkgRemovedFilesKey])
    if len(diffs[kPkgRemovedDirsKey]) != 0:
        new_manifest[kPkgRemovedDirsKey] = list(diffs[kPkgRemovedDirsKey])
    new_manifest[kPkgFilesKey] = diffs[kPkgFilesKey].copy()
    new_manifest[kPkgDirsKey] = diffs[kPkgDirsKey].copy()

#    print "\nPackage diffs = %s\n" % diffs
    new_manifest_string = json.dumps(new_manifest, sort_keys=True,
                                 indent=4, separators=(',', ': '))

    new_tf = tarfile.open(output_file, "w:gz", format = tarfile.PAX_FORMAT)
    mani_file_info = tarfile.TarInfo(name = "+MANIFEST")
    mani_file_info.size = len(new_manifest_string)
    mani_file_info.mode = 0600
    mani_file_info.type = tarfile.REGTYPE
    mani_file = StringIO.StringIO(new_manifest_string)
    new_tf.addfile(mani_file_info, mani_file)
    mani_file.close()

    # Now copy files from pkg2 to new_tf
    # We want to do this by going through pkg2_tarfile.
    search_dict = dict(diffs[kPkgFilesKey], ** diffs[kPkgDirsKey])
    while member is not None:
        fname = member.name if member.name in search_dict else "/" + member.name
        if fname in search_dict:
            if member.issym() or member.islnk():
            # A link
                new_tf.addfile(member)
            elif member.isreg():
            # A regular file.  Copy
                data = pkg2_tarfile.extractfile(member)
                new_tf.addfile(member, data)
            else:
                print >> sys.stderr, "Unknown file type"
                sys.exit(1)
            search_dict.pop(fname)
            if len(search_dict) == 0:
                break
        member = pkg2_tarfile.next()
    new_tf.close()
            
if __name__ == "__main__":
    main()
