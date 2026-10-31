# Adapter incubation and continuity

Harrison Consulting continues developing and certifying selected integrations in
its private development repository while coordinating upstream inclusion and
licensing with Oduist. Repository availability describes source placement;
certification records establish tested behavior. This development arrangement
preserves our collaboration with contributors and providers.

| Integration | Development source | Public continuity |
| --- | --- | --- |
| VoiceTel / VoiceML | `connect_voicetel`, privately supplied | Existing authorship, upstream references and public Git history remain available. |
| Textable | `connect_textable`, privately supplied | The existing technical module name and public Git history remain available. |
| VoIP Innovations | Future adapter work incubates privately | Public provider-interface catalog and mapping remain available under `scripts/provider_graph/`. |
| Yeastar | Planned adapter work incubates privately | Upstream Asterisk reuse and vendor-specific capabilities require qualification. |

The move preserves `connect_voicetel` and `connect_textable` technical names,
XML IDs, model names and migration history. Existing installations retain their
data; operators deploy the paired private source before updating the public
source. Both directories must resolve exactly once on the composed addons path.
This repository supplies documentation at the old boundary, with no installable
replacement or retirement module.

Private repository placement supplies no licence entitlement. Existing source
notices and dependency licences remain in force. The Connect core and catalog
products retain their licence checks. A privately incubated adapter is excluded
from the Oduist sales catalog and identified as a private integration; it is not
represented as a purchased product. Upstream publication and licensing follow
the arrangement agreed with Oduist and the respective contributors.

The provider-interface graph accepts an explicitly supplied private source root:

```sh
python3 scripts/provider_graph.py --root /path/to/connect_addons \
  --addon-root /path/to/private-addons --out /path/to/private-evidence/graph.json
```

Keep graphs generated from private source in private evidence storage. A graph
made without that root reports private source as not supplied; its coverage is
limited to the source it actually scanned.
