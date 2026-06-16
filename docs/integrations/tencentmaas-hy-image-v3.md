# Tencent MaaS TokenHub hy-image-v3.0 Contract

Probe date: 2026-06-16

Probe input: synthetic 512 x 320 PNG generated locally by `api/scripts/probe_image_provider.py`. No user FITS or user image was uploaded during probing.

## Supported Endpoint

- Method: `POST`
- URL: `https://tokenhub.tencentmaas.com/v1/images/generations`
- Request body: JSON
- Auth: `Authorization: Bearer <server-side key>`

Required fields used by Starun:

```json
{
  "model": "hy-image-v3.0",
  "prompt": "...",
  "image": "data:image/png;base64,...",
  "response_format": "b64_json",
  "n": 1
}
```

Observed behavior: despite requesting `b64_json`, the provider returns an HTTPS image URL.

## Unsupported Endpoint

`POST /images/edits` with multipart image returned `404 text/plain`.

## Response Shape

Successful response top-level keys:

```json
["completed_at", "created_at", "data", "object", "request_id", "status"]
```

`data[0]` keys:

```json
["revised_prompt", "url"]
```

Image URL host observed:

```text
aiart-1258344699.cos.ap-guangzhou.myqcloud.com
```

Generated image observed:

- Format: PNG
- Dimensions: 1024 x 1024
- Behavior: synchronous response with a downloadable URL

## Reference Conditioning Conclusion

The provider accepts a reference image in the `image` field. The generated image did not preserve the synthetic input aspect ratio; Starun must treat composition preservation as prompt guidance, not a hard dimension guarantee.

## Adapter Requirements

- Send only server-side requests.
- Use the `image` JSON field for the reference image.
- Request one image only.
- Accept only HTTPS result URLs from configured allowed hosts.
- Download and decode the returned image before accepting it.
- Do not persist full provider responses, authorization headers, or API keys.
