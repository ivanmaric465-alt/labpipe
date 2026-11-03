# Known-answer test of {{method}}

`expected.yaml` names one input file here and the values `labpipe methods check {{method}}`
must get from it (`value` ± `tolerance`). The example is made for the scaffold's template code
(`summary.*`); when you replace the analysis, replace the file and the expected numbers with
one whose result you know independently. A number that drifts after a change fails the check.
