# M03 test inputs

`feed-sample.xml` - a saved copy of the mock feed (`http://mock-api:8080/feed.xml`, 25 synthetic posts).
Run twice: the first run e-mails the matching posts, the second reports "Nothing new" (Remove Duplicates across
previous executions, keyed by guid).
