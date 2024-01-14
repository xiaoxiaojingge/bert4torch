from bert4torch.models.transformer import Decoder
from bert4torch.snippets import delete_arguments, modify_variable_mapping
from bert4torch.layers import BlockIdentity, LlamaFeedForward, NormHead
import torch

class LLaMA(Decoder):
    '''LLaMA
    链接: https://github.com/facebookresearch/llama
    1. 去掉bias
    2. rmsnorm
    3. feedForward不同, 三层全连接
    4. rotary相对位置编码
    '''
    @delete_arguments('with_pool', 'with_mlm', 'with_nsp')
    def __init__(self, *args, p_bias='rotary', **kwargs):
        kwargs.update({'p_bias': p_bias, 'weight': True, 'bias': False, 'norm_mode': 'rmsnorm', 
                       'is_decoder': True, 'final_layernorm': True, 'pre_layernorm': True})
        super().__init__(*args, **kwargs)
        del self.embeddings.layerNorm
        self.prefix = 'llama'

        # 修改feedword
        for layer in self.decoderLayer:
            layer.feedForward = LlamaFeedForward(self.hidden_size, **kwargs)
        
        # 修改lm_head，目前在Baichuan2中使用
        if kwargs.get('norm_head') is True:
            self.lm_head = NormHead(self.hidden_size, self.vocab_size)

    def load_trans_ckpt(self, checkpoint):
        state_dict = super().load_trans_ckpt(checkpoint)
        # baichuan的qkv权重是合在一起的W_pack, 单独处理
        variable_map = {}
        for i in range(self.num_hidden_layers):
            mapping = {f'model.layers.{i}.self_attn.W_pack.weight': 'decoderLayer.{}.multiHeadAttention.{}.weight'}
            for old_key, new_key in mapping.items():
                if (qkv := state_dict.get(old_key)) is None:
                    continue
                qkv = torch.split(qkv, [self.hidden_size, self.hidden_size, self.hidden_size], 0)
                for i_k, i_v in zip(['q','k', 'v'], qkv):
                    state_dict[new_key.format(i, i_k)] = i_v
                state_dict.pop(old_key)
            
            # belle
            old_key = 'model.layers.{}.self_attn.{}_proj.weight'
            new_key = 'decoderLayer.{}.multiHeadAttention.{}.weight'
            for i_k in ['q', 'k', 'v']:
                if state_dict.get(old_key.format(i, i_k)) is not None:
                    variable_map[new_key.format(i, i_k)] = old_key.format(i, i_k)
        self.variable_mapping = modify_variable_mapping(self.variable_mapping, **variable_map)
        return state_dict
    
    def save_trans_ckpt(self):
        '''把q,k,v合并成qkv, 以便于transformers包加载'''
        state_dict = self.state_dict()
        for i in range(self.num_hidden_layers):
            mapping = {'decoderLayer.{}.multiHeadAttention.{}.weight': f'model.layers.{i}.self_attn.W_pack.weight'}
            for old_key, new_key in mapping.items():
                qkv = []
                for i_k in ['q', 'k', 'v']:
                    if old_key.format(i, i_k) in state_dict:
                        qkv.append(state_dict.pop(old_key.format(i, i_k)))
                if qkv:
                    state_dict[new_key] = torch.cat(qkv)
        return state_dict
    
    def variable_mapping(self):
        '''映射到权重格式
        llama一般有两种格式, 一种是huggingface格式, 一种是pth格式, 这里的映射是以hf格式为准
        '''
        mapping = {
            'embeddings.word_embeddings.weight': 'model.embed_tokens.weight',
            'lm_head.weight': 'lm_head.weight',
            'LayerNormFinal.weight': 'model.norm.weight',
            }

        for i in range(self.num_hidden_layers):
            mapping.update( 
            {
            f'decoderLayer.{i}.multiHeadAttention.o.weight': f'model.layers.{i}.self_attn.o_proj.weight',
            f'decoderLayer.{i}.attnLayerNorm.weight': f'model.layers.{i}.input_layernorm.weight',
            f'decoderLayer.{i}.feedForward.intermediateDense.weight': f'model.layers.{i}.mlp.gate_proj.weight',
            f'decoderLayer.{i}.feedForward.intermediateDense2.weight': f'model.layers.{i}.mlp.up_proj.weight',
            f'decoderLayer.{i}.feedForward.outputDense.weight': f'model.layers.{i}.mlp.down_proj.weight',
            f'decoderLayer.{i}.ffnLayerNorm.weight': f'model.layers.{i}.post_attention_layernorm.weight'
            })
        return mapping
